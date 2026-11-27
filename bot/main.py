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

from app.moodle import MoodleAuthError, MoodleClient, MoodleError, Quiz

from .notifier import pick_new
from .store import Store, User

log = logging.getLogger("qcm-bot")


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    moodle_url: str
    moodle_service: str = "moodle_mobile_app"
    discord_token: str
    encryption_key: str  # clé Fernet : voir README
    poll_minutes: int = 10
    db_file: str = "qcm.db"
    max_parallel: int = 5  # connexions simultanées à Moodle


def build_message(q: Quiz) -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(title=q.name, description="Nouveau QCM ouvert !", color=0x5865F2)
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
    username = discord.ui.TextInput(label="Nom d'utilisateur Moodle", max_length=100)
    password = discord.ui.TextInput(label="Mot de passe Moodle", max_length=200)

    def __init__(self, bot: "QcmBot"):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        username, password = self.username.value.strip(), self.password.value
        try:
            await asyncio.to_thread(self.bot.moodle.login, username, password)
        except MoodleAuthError:
            await interaction.followup.send("❌ Identifiants Moodle incorrects. "
                                            "Relance /config pour réessayer.", ephemeral=True)
            return
        except (MoodleError, Exception):
            log.exception("Vérification des identifiants impossible")
            await interaction.followup.send("❌ Moodle est injoignable pour le moment, "
                                            "réessaie plus tard.", ephemeral=True)
            return
        self.bot.store.upsert_user(interaction.user.id, interaction.channel_id,
                                   username, password)
        self.bot.tokens.pop(interaction.user.id, None)
        await interaction.followup.send(
            "✅ Configuration enregistrée. Tes QCM seront annoncés dans ce salon. "
            "Les QCM déjà présents ne déclenchent pas de notification.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Erreur dans le formulaire /config", exc_info=error)


class QcmBot(discord.Client):
    def __init__(self, cfg: BotSettings):
        super().__init__(intents=discord.Intents.default())
        self.cfg = cfg
        self.moodle = MoodleClient(cfg.moodle_url, cfg.moodle_service)
        self.store = Store(cfg.db_file, cfg.encryption_key)
        self.tokens: dict[int, str] = {}
        self.tree = app_commands.CommandTree(self)
        self.sem = asyncio.Semaphore(cfg.max_parallel)
        self.poll.change_interval(minutes=cfg.poll_minutes)

        @self.tree.command(name="config", description="Connecte ton compte Moodle et choisis ce salon pour les alertes")
        async def config(interaction: discord.Interaction):
            await interaction.response.send_modal(ConfigModal(self))

        @self.tree.command(name="stop", description="Supprime tes identifiants et arrête les alertes")
        async def stop(interaction: discord.Interaction):
            self.tokens.pop(interaction.user.id, None)
            ok = self.store.delete_user(interaction.user.id)
            await interaction.response.send_message(
                "🗑️ Tes données ont été supprimées." if ok else "Tu n'es pas configuré.",
                ephemeral=True)

    async def setup_hook(self) -> None:
        await self.tree.sync()
        self.poll.start()

    def _fetch(self, user: User) -> list[Quiz]:
        for attempt in (1, 2):
            try:
                token = self.tokens.get(user.id)
                if not token:
                    token = self.tokens[user.id] = self.moodle.login(user.username, user.password)
                return self.moodle.get_quizzes(token)
            except MoodleAuthError:
                self.tokens.pop(user.id, None)  # token expiré : une reconnexion, sinon échec
                if attempt == 2:
                    raise
        return []

    async def _process(self, user: User) -> None:
        try:
            async with self.sem:
                quizzes = await asyncio.to_thread(self._fetch, user)
        except MoodleAuthError:
            self.store.deactivate(user.id)
            await self._send(user, content=f"<@{user.id}> ⚠️ Tes identifiants Moodle ne "
                             "fonctionnent plus, les alertes sont suspendues. Relance /config.")
            return
        except Exception:
            log.exception("Échec Moodle pour l'utilisateur %s", user.id)
            return

        if not user.seeded:  # premier passage : on mémorise l'existant sans notifier
            self.store.mark_notified(user.id, [q.id for q in quizzes])
            self.store.mark_seeded(user.id)
            return
        to_send, to_skip = pick_new(quizzes, self.store.notified(user.id))
        self.store.mark_notified(user.id, to_skip)
        for q in to_send:
            embed, view = build_message(q)
            if not await self._send(user, content=f"<@{user.id}>", embed=embed, view=view):
                break  # salon inaccessible : on réessaiera au prochain cycle
            self.store.mark_notified(user.id, [q.id])
            log.info("QCM %s notifié à %s", q.id, user.id)

    async def _send(self, user: User, **kwargs) -> bool:
        try:
            channel = self.get_channel(user.channel_id) or await self.fetch_channel(user.channel_id)
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
