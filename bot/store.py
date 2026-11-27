"""Stockage SQLite des utilisateurs (identifiants chiffrés, salon) et des QCM déjà notifiés."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from cryptography.fernet import Fernet


@dataclass
class User:
    id: int
    channel_id: int
    moodle_url: str
    username: str
    password: str
    seeded: bool


class Store:
    def __init__(self, path: str, key: str):
        self.fernet = Fernet(key.encode())
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL,
                moodle_url TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL, password BLOB NOT NULL,
                seeded INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS notified (
                user_id INTEGER NOT NULL, quiz_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, quiz_id));
        """)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(users)")}
        if "moodle_url" not in cols:  # base créée avant l'ajout du choix d'URL
            self.db.execute("ALTER TABLE users ADD COLUMN moodle_url TEXT NOT NULL DEFAULT ''")

    def upsert_user(self, user_id: int, channel_id: int, moodle_url: str,
                    username: str, password: str) -> None:
        """Enregistre (ou met à jour) un utilisateur. Si le compte Moodle change,
        l'historique est effacé et le prochain cycle ne notifie que les nouveautés."""
        old = self.db.execute("SELECT moodle_url, username FROM users WHERE id=?",
                              (user_id,)).fetchone()
        enc = self.fernet.encrypt(password.encode())
        if old and old == (moodle_url, username):
            self.db.execute("UPDATE users SET channel_id=?, password=?, active=1 WHERE id=?",
                            (channel_id, enc, user_id))
        else:
            self.db.execute("DELETE FROM notified WHERE user_id=?", (user_id,))
            self.db.execute(
                "INSERT OR REPLACE INTO users"
                " (id, channel_id, moodle_url, username, password, seeded, active)"
                " VALUES (?,?,?,?,?,0,1)", (user_id, channel_id, moodle_url, username, enc))
        self.db.commit()

    def active_users(self) -> list[User]:
        rows = self.db.execute(
            "SELECT id, channel_id, moodle_url, username, password, seeded"
            " FROM users WHERE active=1").fetchall()
        return [User(r[0], r[1], r[2], r[3], self.fernet.decrypt(r[4]).decode(), bool(r[5]))
                for r in rows]

    def delete_user(self, user_id: int) -> bool:
        self.db.execute("DELETE FROM notified WHERE user_id=?", (user_id,))
        n = self.db.execute("DELETE FROM users WHERE id=?", (user_id,)).rowcount
        self.db.commit()
        return n > 0

    def deactivate(self, user_id: int) -> None:
        self.db.execute("UPDATE users SET active=0 WHERE id=?", (user_id,))
        self.db.commit()

    def notified(self, user_id: int) -> set[int]:
        return {r[0] for r in self.db.execute(
            "SELECT quiz_id FROM notified WHERE user_id=?", (user_id,))}

    def mark_notified(self, user_id: int, quiz_ids) -> None:
        self.db.executemany("INSERT OR IGNORE INTO notified VALUES (?,?)",
                            [(user_id, i) for i in quiz_ids])
        self.db.commit()

    def mark_seeded(self, user_id: int) -> None:
        self.db.execute("UPDATE users SET seeded=1 WHERE id=?", (user_id,))
        self.db.commit()
