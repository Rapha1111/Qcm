"""Bot Discord : scrute Moodle toutes les N minutes et notifie les nouveaux QCM ouverts."""
from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import tasks
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.moodle import MoodleAuthError, MoodleClient, Quiz

from .notifier import State, find_new

log = logging.getLogger("qcm-bot")


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    moodle_url: str
    moodle_service: str = "moodle_mobile_app"
    moodle_username: str
    moodle_password: str
    discord_token: str
    discord_channel_id: int
    poll_minutes: int = 10
    state_file: str = "state.json"


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


class QcmBot(discord.Client):
    def __init__(self, cfg: BotSettings):
        super().__init__(intents=discord.Intents.default())
        self.cfg = cfg
        self.moodle = MoodleClient(cfg.moodle_url, cfg.moodle_service)
        self.state = State(cfg.state_file)
        self.token: str | None = None
        self.poll.change_interval(minutes=cfg.poll_minutes)

    async def setup_hook(self) -> None:
        self.poll.start()

    def _fetch(self) -> list[Quiz]:
        for attempt in (1, 2):
            try:
                if not self.token:
                    self.token = self.moodle.login(self.cfg.moodle_username,
                                                   self.cfg.moodle_password)
                return self.moodle.get_quizzes(self.token)
            except MoodleAuthError:
                self.token = None  # token expiré : on se reconnecte une fois
                if attempt == 2:
                    raise
        return []

    @tasks.loop(minutes=10)
    async def poll(self) -> None:
        try:
            quizzes = await asyncio.to_thread(self._fetch)
        except Exception:
            log.exception("Échec de la récupération des QCM, nouvel essai au prochain cycle")
            return
        channel = self.get_channel(self.cfg.discord_channel_id) or \
            await self.fetch_channel(self.cfg.discord_channel_id)
        for q in find_new(quizzes, self.state):
            embed, view = build_message(q)
            await channel.send(embed=embed, view=view)
            log.info("Notification envoyée : %s", q.name)

    @poll.before_loop
    async def _wait(self) -> None:
        await self.wait_until_ready()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = BotSettings()
    QcmBot(cfg).run(cfg.discord_token)


if __name__ == "__main__":
    main()
