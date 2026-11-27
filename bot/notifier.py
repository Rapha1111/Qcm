"""Logique de sélection des QCM à notifier (sans dépendance Discord, donc testable)."""
from __future__ import annotations

from app.moodle import Quiz


def pick_new(quizzes: list[Quiz], notified: set[int]) -> tuple[list[Quiz], set[int]]:
    """Renvoie (QCM ouverts à notifier, IDs à marquer vus sans notification).

    Un QCM pas encore ouvert est ignoré : il sera notifié à son ouverture.
    Un QCM déjà fermé jamais vu est marqué vu sans notification."""
    to_send = [q for q in quizzes if q.id not in notified and q.status == "open"]
    to_skip = {q.id for q in quizzes if q.id not in notified and q.status == "closed"}
    return to_send, to_skip
