"""Stockage SQLite : configuration par serveur (token chiffré, salon) et QCM à venir."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from cryptography.fernet import Fernet

from app.moodle import Quiz


@dataclass
class Guild:
    id: int
    channel_id: int
    admin_id: int  # administrateur qui a lancé /config
    moodle_url: str
    account: str  # identifiant Moodle lié au token
    token: str


class Store:
    def __init__(self, path: str, key: str):
        self.fernet = Fernet(key.encode())
        self.db = sqlite3.connect(path)
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='users'").fetchone():
            # Ancienne version (configuration par utilisateur) : on repart de zéro.
            self.db.executescript("DROP TABLE users; DROP TABLE IF EXISTS notified;"
                                  " DROP TABLE IF EXISTS upcoming;")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS guilds (
                id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, admin_id INTEGER NOT NULL,
                moodle_url TEXT NOT NULL, account TEXT NOT NULL, token BLOB NOT NULL,
                active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS quizzes (
                guild_id INTEGER NOT NULL, quiz_id INTEGER NOT NULL, course_id INTEGER NOT NULL,
                course_name TEXT NOT NULL, name TEXT NOT NULL, url TEXT NOT NULL,
                time_open INTEGER NOT NULL, time_close INTEGER NOT NULL,
                time_limit INTEGER NOT NULL, PRIMARY KEY (guild_id, quiz_id));
        """)

    # --- serveurs ---
    def upsert_guild(self, guild_id: int, channel_id: int, admin_id: int,
                     moodle_url: str, account: str, token: str) -> None:
        """Enregistre la configuration d'un serveur. Si le compte ou le Moodle change,
        les QCM mémorisés sont effacés."""
        old = self.db.execute("SELECT moodle_url, account FROM guilds WHERE id=?",
                              (guild_id,)).fetchone()
        if old != (moodle_url, account):
            self.db.execute("DELETE FROM quizzes WHERE guild_id=?", (guild_id,))
        self.db.execute("INSERT OR REPLACE INTO guilds VALUES (?,?,?,?,?,?,1)",
                        (guild_id, channel_id, admin_id, moodle_url, account,
                         self.fernet.encrypt(token.encode())))
        self.db.commit()

    def _guild(self, row) -> Guild | None:
        return Guild(row[0], row[1], row[2], row[3], row[4],
                     self.fernet.decrypt(row[5]).decode()) if row else None

    _COLS = "id, channel_id, admin_id, moodle_url, account, token"

    def get_guild(self, guild_id: int) -> Guild | None:
        return self._guild(self.db.execute(
            f"SELECT {self._COLS} FROM guilds WHERE id=?", (guild_id,)).fetchone())

    def active_guilds(self) -> list[Guild]:
        return [self._guild(r) for r in self.db.execute(
            f"SELECT {self._COLS} FROM guilds WHERE active=1")]

    def deactivate(self, guild_id: int) -> None:
        self.db.execute("UPDATE guilds SET active=0 WHERE id=?", (guild_id,))
        self.db.commit()

    def delete_guild(self, guild_id: int) -> bool:
        self.db.execute("DELETE FROM quizzes WHERE guild_id=?", (guild_id,))
        n = self.db.execute("DELETE FROM guilds WHERE id=?", (guild_id,)).rowcount
        self.db.commit()
        return n > 0

    # --- QCM à venir ---
    def replace_quizzes(self, guild_id: int, quizzes: list[Quiz]) -> None:
        """Remplace les QCM mémorisés d'un serveur par la liste donnée."""
        self.db.execute("DELETE FROM quizzes WHERE guild_id=?", (guild_id,))
        self.db.executemany(
            "INSERT INTO quizzes VALUES (?,?,?,?,?,?,?,?,?)",
            [(guild_id, q.id, q.course_id, q.course_name, q.name, q.url,
              q.time_open, q.time_close, q.time_limit) for q in quizzes])
        self.db.commit()

    def count_quizzes(self, guild_id: int) -> int:
        return self.db.execute("SELECT COUNT(*) FROM quizzes WHERE guild_id=?",
                               (guild_id,)).fetchone()[0]

    def due(self, now: float) -> list[tuple[int, Quiz]]:
        """QCM dont l'heure d'ouverture est atteinte."""
        rows = self.db.execute(
            "SELECT guild_id, quiz_id, course_id, course_name, name, url, time_open,"
            " time_close, time_limit FROM quizzes WHERE time_open <= ? ORDER BY time_open", (now,))
        return [(r[0], Quiz(*r[1:])) for r in rows]

    def delete_quiz(self, guild_id: int, quiz_id: int) -> None:
        self.db.execute("DELETE FROM quizzes WHERE guild_id=? AND quiz_id=?", (guild_id, quiz_id))
        self.db.commit()
