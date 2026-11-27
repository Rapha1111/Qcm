"""Logique de détection des nouveaux QCM (sans dépendance Discord, donc testable)."""
from __future__ import annotations

import json
from pathlib import Path

from app.moodle import Quiz


class State:
    """Mémorise sur disque les QCM déjà notifiés."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.first_run = not self.path.exists()
        self.notified: set[int] = set()
        if not self.first_run:
            self.notified = set(json.loads(self.path.read_text()))

    def save(self) -> None:
        self.path.write_text(json.dumps(sorted(self.notified)))


def find_new(quizzes: list[Quiz], state: State) -> list[Quiz]:
    """QCM ouverts jamais notifiés. Les QCM déjà fermés sont marqués vus sans notification ;
    au tout premier lancement, tout l'existant est marqué vu pour ne pas spammer."""
    if state.first_run:
        state.notified = {q.id for q in quizzes}
        state.first_run = False
        state.save()
        return []
    new = []
    for q in quizzes:
        if q.id in state.notified or q.status == "upcoming":
            continue
        state.notified.add(q.id)
        if q.status == "open":
            new.append(q)
    state.save()
    return new
