"""Stockage SQLite des utilisateurs (token Moodle chiffré, salon) et des QCM déjà traités."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from cryptography.fernet import Fernet


@dataclass
class User:
    id: int
    channel_id: int
    moodle_url: str
    account: str  # identifiant Moodle lié au token (affichage / détection de changement)
    token: str
    seeded: bool


class Store:
    def __init__(self, path: str, key: str):
        self.fernet = Fernet(key.encode())
        self.db = sqlite3.connect(path)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(users)")}
        if cols and "token" not in cols:
            # Ancienne version (mots de passe) : on supprime tout, chacun refait /config.
            self.db.executescript("DROP TABLE users; DELETE FROM notified;"
                                  if self._has("notified") else "DROP TABLE users;")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL,
                moodle_url TEXT NOT NULL, account TEXT NOT NULL, token BLOB NOT NULL,
                seeded INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS notified (
                user_id INTEGER NOT NULL, quiz_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, quiz_id));
            CREATE TABLE IF NOT EXISTS upcoming (
                user_id INTEGER NOT NULL, quiz_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, quiz_id));
        """)

    def _has(self, table: str) -> bool:
        return bool(self.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone())

    def _clear(self, user_id: int) -> None:
        self.db.execute("DELETE FROM notified WHERE user_id=?", (user_id,))
        self.db.execute("DELETE FROM upcoming WHERE user_id=?", (user_id,))

    def upsert_user(self, user_id: int, channel_id: int, moodle_url: str,
                    account: str, token: str) -> None:
        """Enregistre (ou met à jour) un utilisateur. Si le compte ou le Moodle change,
        l'historique est effacé et le premier passage ne notifie rien."""
        old = self.db.execute("SELECT moodle_url, account FROM users WHERE id=?",
                              (user_id,)).fetchone()
        enc = self.fernet.encrypt(token.encode())
        if old and old == (moodle_url, account):
            self.db.execute("UPDATE users SET channel_id=?, token=?, active=1 WHERE id=?",
                            (channel_id, enc, user_id))
        else:
            self._clear(user_id)
            self.db.execute(
                "INSERT OR REPLACE INTO users"
                " (id, channel_id, moodle_url, account, token, seeded, active)"
                " VALUES (?,?,?,?,?,0,1)", (user_id, channel_id, moodle_url, account, enc))
        self.db.commit()

    def active_users(self) -> list[User]:
        rows = self.db.execute(
            "SELECT id, channel_id, moodle_url, account, token, seeded"
            " FROM users WHERE active=1").fetchall()
        return [User(r[0], r[1], r[2], r[3], self.fernet.decrypt(r[4]).decode(), bool(r[5]))
                for r in rows]

    def delete_user(self, user_id: int) -> bool:
        self._clear(user_id)
        n = self.db.execute("DELETE FROM users WHERE id=?", (user_id,)).rowcount
        self.db.commit()
        return n > 0

    def deactivate(self, user_id: int) -> None:
        self.db.execute("UPDATE users SET active=0 WHERE id=?", (user_id,))
        self.db.commit()

    def _ids(self, table: str, user_id: int) -> set[int]:
        return {r[0] for r in self.db.execute(
            f"SELECT quiz_id FROM {table} WHERE user_id=?", (user_id,))}

    def _add(self, table: str, user_id: int, quiz_ids) -> None:
        self.db.executemany(f"INSERT OR IGNORE INTO {table} VALUES (?,?)",
                            [(user_id, i) for i in quiz_ids])
        self.db.commit()

    def notified(self, user_id: int) -> set[int]:
        return self._ids("notified", user_id)

    def mark_notified(self, user_id: int, quiz_ids) -> None:
        self._add("notified", user_id, quiz_ids)

    def upcoming(self, user_id: int) -> set[int]:
        return self._ids("upcoming", user_id)

    def track_upcoming(self, user_id: int, quiz_ids) -> None:
        self._add("upcoming", user_id, quiz_ids)

    def mark_seeded(self, user_id: int) -> None:
        self.db.execute("UPDATE users SET seeded=1 WHERE id=?", (user_id,))
        self.db.commit()
