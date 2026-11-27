"""Bot Discord multi-utilisateurs : chaque utilisateur configure ses identifiants Moodle avec
/config ; le bot scrute Moodle toutes les N minutes et poste les nouveaux QCM dans le salon
où /config a été lancé."""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import tasks
from pydantic_settings import BaseSettings, SettingsConfigDict

import time

from app.moodle import MoodleAuthError, MoodleClient, Quiz, parse_token

from app.urlcheck import ensure_public_host, normalize_moodle_url

from .notifier import pick_new, seed
from .store import Store, User

log = logging.getLogger("qcm-bot")

ROLE_NAME = "notif qcm"


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    moodle_url: str = ""  # URL proposée par défaut dans /config (facultatif)
    moodle_service: str = "moodle_mobile_app"
    discord_token: str
    encryption_key: str  # clé Fernet : voir README
    poll_minutes: int = 10
    db_file: str = "qcm.db"
    max_parallel: int = 5  # connexions simultanées à Moodle


def build_message(q: Quiz) -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(title=q.name, description="Ce QCM vient de s'ouvrir !", color=0x5865F2)
    embed.add_field(name="Matière", value=q.course_name or "—", inline=False)
    if q.time_close:
        close = f"<t:{q.time_close}:F> (<t:{q.time_close}:R>)"
    else:
        close = "Pas de date de fermeture"
    embed.add_field(name="Fermeture", value=close, inline=False)
    if q.time_limit:
        embed.add_field(name="Durée", value=f"{q.time_limit // 60} min", inline=False)
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Résoudre le QCM", url=q.url,
                                    style=discord.ButtonStyle.link))
    return embed, view


class ConfigModal(discord.ui.Modal, title="Connexion à Moodle"):
    url = discord.ui.TextInput(label="URL de ton Moodle", max_length=200,
                               placeholder="https://moodle.monecole.fr")
    token = discord.ui.TextInput(label="Token Moodle (voir /aide)", max_length=600)

    def __init__(self, bot: "QcmBot"):
        super().__init__()
        self.bot = bot
        self.url.default = bot.cfg.moodle_url or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            url = normalize_moodle_url(self.url.value)
            await asyncio.to_thread(ensure_public_host, url)
            token = parse_token(self.token.value)
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}. Voir /aide pour récupérer ton token.",
                                            ephemeral=True)
            return
        try:
            info = await asyncio.to_thread(self.bot.client_for(url).site_info, token)
        except MoodleAuthError:
            await interaction.followup.send("❌ Token refusé par Moodle (invalide ou expiré). "
                                            "Voir /aide.", ephemeral=True)
            return
        except Exception:
            log.exception("Vérification du token impossible")
            await interaction.followup.send(
                "❌ Impossible de joindre Moodle à cette adresse (URL incorrecte, services web "
                "désactivés ou site indisponible).", ephemeral=True)
            return
        self.bot.store.upsert_user(interaction.user.id, interaction.channel_id, url,
                                   info.get("username", str(info.get("userid"))), token)
        await interaction.followup.send(
            f"✅ Connecté en tant que **{info.get('fullname', '?')}**. Tes QCM seront annoncés "
            "dans ce salon dès qu'ils s'ouvrent.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Erreur dans le formulaire /config", exc_info=error)


HELP = (
    "**Récupérer ton token Moodle**\n"
    "**Méthode 1** : *Préférences → Sécurité → Clés de sécurité* (`/user/managetoken.php`). "
    "Si tu y vois une clé « Moodle mobile web service », copie-la. Si tu n'as que la clé *RSS*, "
    "elle ne marche pas : passe à la méthode 2.\n"
    "**Méthode 2** (marche aussi avec une connexion SSO) : connecté à ton Moodle, ouvre "
    "`https://<ton-moodle>/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=1"
    "&urlscheme=moodlemobile`. Avant, ouvre les outils du navigateur (F12 → onglet *Réseau*, coche "
    "*Conserver le journal*). Clique sur la requête `launch.php` → *En-têtes de réponse* → "
    "copie la valeur de `Location` (elle commence par `moodlemobile://token=`) et colle-la dans "
    "/config.\n"
    "Le token donne accès à ton compte : ne le partage qu'avec ce bot, et supprime-le avec /stop.")


class QcmBot(discord.Client):
    def __init__(self, cfg: BotSettings):
        super().__init__(intents=discord.Intents.default())
        self.cfg = cfg
        self.clients: dict[str, MoodleClient] = {}
        self.store = Store(cfg.db_file, cfg.encryption_key)
        self.tree = app_commands.CommandTree(self)
        self.sem = asyncio.Semaphore(cfg.max_parallel)
        self.poll.change_interval(minutes=cfg.poll_minutes)

        @self.tree.command(name="config", description="Connecte ton compte Moodle et choisis ce salon pour les alertes")
        async def config(interaction: discord.Interaction):
            await interaction.response.send_modal(ConfigModal(self))

        @self.tree.command(name="notif", description="Active ou désactive les mentions pour les nouveaux QCM")
        @app_commands.guild_only()
        async def notif(interaction: discord.Interaction):
            member = interaction.user
            try:
                role = await self.ensure_role(interaction.guild)
                if role in member.roles:
                    await member.remove_roles(role, reason="/notif")
                    msg = f"🔕 Le rôle **{ROLE_NAME}** t'a été retiré, tu ne seras plus mentionné."
                else:
                    await member.add_roles(role, reason="/notif")
                    msg = f"🔔 Tu as le rôle **{ROLE_NAME}** : tu seras mentionné à chaque ouverture de QCM."
            except discord.Forbidden:
                msg = ("❌ Il me manque la permission « Gérer les rôles » (et mon rôle doit être "
                       "placé au-dessus de « %s » dans la liste des rôles du serveur)." % ROLE_NAME)
            await interaction.response.send_message(msg, ephemeral=True)

        @self.tree.command(name="aide", description="Comment récupérer ton token Moodle")
        async def aide(interaction: discord.Interaction):
            await interaction.response.send_message(HELP, ephemeral=True)

        @self.tree.command(name="stop", description="Supprime ton token et arrête les alertes")
        async def stop(interaction: discord.Interaction):
            ok = self.store.delete_user(interaction.user.id)
            await interaction.response.send_message(
                "🗑️ Tes données ont été supprimées." if ok else "Tu n'es pas configuré.",
                ephemeral=True)

    def client_for(self, url: str) -> MoodleClient:
        if url not in self.clients:
            self.clients[url] = MoodleClient(url, self.cfg.moodle_service)
        return self.clients[url]

    async def ensure_role(self, guild: discord.Guild) -> discord.Role:
        """Renvoie le rôle de notification du serveur, en le créant s'il n'existe pas."""
        role = discord.utils.get(guild.roles, name=ROLE_NAME)
        if role is None:
            role = await guild.create_role(name=ROLE_NAME, mentionable=True,
                                           reason="Rôle de notification des QCM")
        return role

    async def _ensure_all_roles(self) -> None:
        for guild in self.guilds:
            try:
                await self.ensure_role(guild)
            except discord.HTTPException:
                log.warning("Impossible de créer le rôle dans %s (permission « Gérer les rôles » ?)",
                            guild.name)

    async def on_ready(self) -> None:
        await self._ensure_all_roles()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._ensure_all_roles()

    async def setup_hook(self) -> None:
        await self.tree.sync()
        self.poll.start()

    async def _process(self, user: User) -> None:
        moodle = self.client_for(user.moodle_url or self.cfg.moodle_url)
        try:
            async with self.sem:
                quizzes = await asyncio.to_thread(moodle.get_quizzes, user.token)
        except MoodleAuthError:
            self.store.deactivate(user.id)
            await self._send(user, content=f"<@{user.id}> ⚠️ Ton token Moodle n'est plus valide, "
                             "les alertes sont suspendues. Relance /config avec un nouveau token "
                             "(voir /aide).")
            return
        except Exception:
            log.exception("Échec Moodle pour l'utilisateur %s", user.id)
            return

        if not user.seeded:  # premier passage : on mémorise l'existant sans notifier
            seen, upcoming = seed(quizzes)
            self.store.mark_notified(user.id, seen)
            self.store.track_upcoming(user.id, upcoming)
            self.store.mark_seeded(user.id)
            return
        window = 2 * self.cfg.poll_minutes * 60 + 60
        plan = pick_new(quizzes, self.store.notified(user.id), self.store.upcoming(user.id),
                        time.time(), window)
        self.store.mark_notified(user.id, plan.skip)
        self.store.track_upcoming(user.id, plan.track)
        for q in plan.send:
            embed, view = build_message(q)
            if not await self._send(user, ping=True, embed=embed, view=view):
                break  # salon inaccessible : on réessaiera au prochain cycle
            self.store.mark_notified(user.id, [q.id])
            log.info("QCM %s notifié à %s", q.id, user.id)

    async def _send(self, user: User, **kwargs) -> bool:
        try:
            channel = self.get_channel(user.channel_id) or await self.fetch_channel(user.channel_id)
            if kwargs.pop("ping", False):  # mention du rôle s'il existe, sinon de l'utilisateur
                guild = getattr(channel, "guild", None)
                role = discord.utils.get(guild.roles, name=ROLE_NAME) if guild else None
                kwargs["content"] = role.mention if role else f"<@{user.id}>"
            await channel.send(**kwargs)
            return True
        except (discord.Forbidden, discord.NotFound):
            log.warning("Salon %s inaccessible pour l'utilisateur %s", user.channel_id, user.id)
        except discord.HTTPException:
            log.exception("Envoi Discord échoué")
        return False

    @tasks.loop(minutes=10)
    async def poll(self) -> None:
        await asyncio.gather(*(self._process(u) for u in self.store.active_users()))

    @poll.before_loop
    async def _wait(self) -> None:
        await self.wait_until_ready()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = BotSettings()
    QcmBot(cfg).run(cfg.discord_token)


if __name__ == "__main__":
    main()
