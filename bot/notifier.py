"""Logique de sélection des QCM à notifier (sans dépendance Discord, donc testable)."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.moodle import Quiz


@dataclass
class Plan:
    send: list[Quiz] = field(default_factory=list)  # à notifier maintenant
    skip: set[int] = field(default_factory=set)  # à marquer vus sans notification
    track: set[int] = field(default_factory=set)  # à venir : à surveiller jusqu'à l'ouverture


def seed(quizzes: list[Quiz]) -> tuple[set[int], set[int]]:
    """Premier passage : (QCM à marquer vus, QCM à venir à surveiller). Les QCM pas encore
    ouverts restent donc notifiables à leur ouverture."""
    return ({q.id for q in quizzes if q.status != "upcoming"},
            {q.id for q in quizzes if q.status == "upcoming"})


def pick_new(quizzes: list[Quiz], notified: set[int], upcoming: set[int],
             now: float, window: float) -> Plan:
    """Notifie un QCM au moment où il se lance :
    - à venir : surveillé, notifié dès qu'il s'ouvre ;
    - ouvert depuis moins de `window` secondes (ou sans date d'ouverture) : notifié ;
    - ouvert depuis longtemps ou fermé, découvert tardivement : ignoré sans notification."""
    plan = Plan()
    for q in quizzes:
        if q.id in notified:
            continue
        if q.status == "upcoming":
            plan.track.add(q.id)
        elif q.status == "closed":
            plan.skip.add(q.id)
        elif q.id in upcoming or q.time_open == 0 or now - q.time_open <= window:
            plan.send.append(q)
        else:
            plan.skip.add(q.id)
    return plan
