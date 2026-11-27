"""Bot Discord multi-serveurs : un administrateur lie un compte Moodle à son serveur avec
/config. Chaque jour, le bot mémorise les QCM à venir ; à l'heure d'ouverture, il poste le message
dans le salon configuré en mentionnant le rôle « notif qcm », puis oublie le QCM."""
from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import tasks
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.moodle import MoodleAuthError, MoodleClient, Quiz, parse_token
from app.urlcheck import ensure_public_host, normalize_moodle_url

from .store import Guild, Store

log = logging.getLogger("qcm-bot")

ROLE_NAME = "notif qcm"


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    moodle_url: str = ""  # URL proposée par défaut dans /config (facultatif)
    moodle_service: str = "moodle_mobile_app"
    discord_token: str
    encryption_key: str  # clé Fernet : voir README
    sync_hours: int = 24  # fréquence de récupération des QCM à venir sur Moodle
    db_file: str = "qcm.db"
    max_parallel: int = 5  # connexions simultanées à Moodle


def build_message(q: Quiz, ongoing: bool = False) -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(title=q.name, color=0x5865F2,
                          description="QCM en cours" if ongoing else "Ce QCM vient de s'ouvrir !")
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
        gid = interaction.guild_id
        self.bot.store.upsert_guild(gid, interaction.channel_id, interaction.user.id, url,
                                    info.get("username", str(info.get("userid"))), token)
        try:
            await self.bot.ensure_role(interaction.guild)
        except discord.HTTPException:
            log.warning("Rôle non créé sur %s", gid)
        result = await self.bot.sync_guild(self.bot.store.get_guild(gid))
        upcoming, ongoing = result if result else ([], [])
        extra = (f" {len(upcoming)} QCM à venir mémorisé(s), {len(ongoing)} en cours."
                 if result else "")
        await interaction.followup.send(
            f"✅ Connecté en tant que **{info.get('fullname', '?')}**. Les QCM seront annoncés "
            f"dans ce salon, avec le rôle **{ROLE_NAME}** (`/notif` pour le recevoir).{extra}",
            ephemeral=True)
        for q in ongoing:  # QCM déjà ouverts : renvoyés dans le salon (sans mention du rôle)
            embed, view = build_message(q, ongoing=True)
            await self.bot._send(interaction.channel_id, embed=embed, view=view)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Erreur dans le formulaire /config", exc_info=error)


HELP = (
    "**Récupérer le token Moodle** (réservé aux administrateurs du serveur : le compte lié sert à tout le monde)\n"
    "**Méthode 1** : *Préférences → Sécurité → Clés de sécurité* (`/user/managetoken.php`). "
    "Si tu y vois une clé « Moodle mobile web service », copie-la. Si tu n'as que la clé *RSS*, "
    "elle ne marche pas : passe à la méthode 2.\n"
    "**Méthode 2** (marche aussi avec une connexion SSO) : connecté à ton Moodle, ouvre "
    "`https://<ton-moodle>/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=1"
    "&urlscheme=moodlemobile`. Avant, ouvre les outils du navigateur (F12 → onglet *Réseau*, coche "
    "*Conserver le journal*). Clique sur la requête `launch.php` → *En-têtes de réponse* → "
    "copie la valeur de `Location` (elle commence par `moodlemobile://token=`) et colle-la dans "
    "/config.\n"
    "Le token donne accès à ton compte : ne le partage qu'avec ce bot ; un administrateur peut le supprimer avec /stop.")


class QcmBot(discord.Client):
    def __init__(self, cfg: BotSettings):
        super().__init__(intents=discord.Intents.default())
        self.cfg = cfg
        self.clients: dict[str, MoodleClient] = {}
        self.store = Store(cfg.db_file, cfg.encryption_key)
        self.tree = app_commands.CommandTree(self)
        self.sem = asyncio.Semaphore(cfg.max_parallel)
        self.sync_all.change_interval(hours=cfg.sync_hours)

        def is_admin(i: discord.Interaction) -> bool:
            return i.guild is not None and i.user.guild_permissions.administrator

        @self.tree.command(name="config", description="(Admin) Lie un compte Moodle à ce serveur et choisis ce salon pour les alertes")
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def config(interaction: discord.Interaction):
            if not is_admin(interaction):
                await interaction.response.send_message(
                    "❌ Réservé aux administrateurs du serveur.", ephemeral=True)
                return
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

        @self.tree.command(name="aide", description="Comment récupérer le token Moodle")
        async def aide(interaction: discord.Interaction):
            await interaction.response.send_message(HELP, ephemeral=True)

        @self.tree.command(name="stop", description="(Admin) Supprime le token Moodle de ce serveur et arrête les alertes")
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def stop(interaction: discord.Interaction):
            if not is_admin(interaction):
                await interaction.response.send_message(
                    "❌ Réservé aux administrateurs du serveur.", ephemeral=True)
                return
            ok = self.store.delete_guild(interaction.guild_id)
            await interaction.response.send_message(
                "🗑️ Configuration supprimée." if ok else "Ce serveur n'est pas configuré.",
                ephemeral=True)

    # --- rôle ---
    async def ensure_role(self, guild: discord.Guild) -> discord.Role:
        """Renvoie le rôle de notification du serveur, en le créant s'il n'existe pas."""
        role = discord.utils.get(guild.roles, name=ROLE_NAME)
        if role is None:
            role = await guild.create_role(name=ROLE_NAME, mentionable=True,
                                           reason="Rôle de notification des QCM")
        return role

    async def _ensure_all_roles(self) -> None:
        for guild in self.guilds:
            if self.store.get_guild(guild.id) is None:
                continue  # serveur non configuré : on ne touche à rien
            try:
                await self.ensure_role(guild)
            except discord.HTTPException:
                log.warning("Impossible de créer le rôle dans %s (permission « Gérer les rôles » ?)",
                            guild.name)

    async def on_ready(self) -> None:
        await self._ensure_all_roles()

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self.store.delete_guild(guild.id)  # le bot a été retiré : on supprime le token

    async def setup_hook(self) -> None:
        await self.tree.sync()
        self.sync_all.start()
        self.announce.start()

    def client_for(self, url: str) -> MoodleClient:
        if url not in self.clients:
            self.clients[url] = MoodleClient(url, self.cfg.moodle_service)
        return self.clients[url]

    # --- envoi ---
    async def _send(self, channel_id: int, ping_role: bool = False, **kwargs) -> bool:
        try:
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
            if ping_role:
                role = discord.utils.get(channel.guild.roles, name=ROLE_NAME)
                if role:
                    kwargs["content"] = role.mention
            await channel.send(**kwargs)
            return True
        except (discord.Forbidden, discord.NotFound):
            log.warning("Salon %s inaccessible", channel_id)
        except discord.HTTPException:
            log.exception("Envoi Discord échoué")
        return False

    # --- récupération quotidienne des QCM à venir ---
    async def sync_guild(self, g: Guild) -> tuple[list[Quiz], list[Quiz]] | None:
        """Mémorise les QCM pas encore ouverts du serveur.
        Renvoie (QCM à venir, QCM en cours), ou None en cas d'échec."""
        moodle = self.client_for(g.moodle_url)
        try:
            async with self.sem:
                quizzes = await asyncio.to_thread(moodle.get_quizzes, g.token)
        except MoodleAuthError:
            self.store.deactivate(g.id)
            await self._send(g.channel_id, content=f"<@{g.admin_id}> ⚠️ Le token Moodle n'est "
                             "plus valide, la récupération des QCM est suspendue. Relance /config "
                             "avec un nouveau token (voir /aide).")
            return None
        except Exception:
            log.exception("Échec Moodle pour le serveur %s", g.id)
            return None
        upcoming = [q for q in quizzes if q.status == "upcoming"]
        self.store.replace_quizzes(g.id, upcoming)
        log.info("Serveur %s : %d QCM à venir mémorisés", g.id, len(upcoming))
        return upcoming, [q for q in quizzes if q.status == "open"]

    @tasks.loop(hours=24)
    async def sync_all(self) -> None:
        await asyncio.gather(*(self.sync_guild(g) for g in self.store.active_guilds()))

    # --- envoi à l'heure d'ouverture ---
    @tasks.loop(minutes=1)
    async def announce(self) -> None:
        now = time.time()
        for guild_id, q in self.store.due(now):
            if q.time_close and q.time_close < now:  # déjà fermé (bot arrêté longtemps)
                self.store.delete_quiz(guild_id, q.id)
                continue
            g = self.store.get_guild(guild_id)
            if g is None:
                self.store.delete_quiz(guild_id, q.id)
                continue
            embed, view = build_message(q)
            if await self._send(g.channel_id, ping_role=True, embed=embed, view=view):
                self.store.delete_quiz(guild_id, q.id)
                log.info("QCM %s annoncé sur le serveur %s", q.id, guild_id)

    @sync_all.before_loop
    @announce.before_loop
    async def _wait(self) -> None:
        await self.wait_until_ready()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = BotSettings()
    QcmBot(cfg).run(cfg.discord_token)


if __name__ == "__main__":
    main()
