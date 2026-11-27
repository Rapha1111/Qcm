"""Client minimal pour l'API de services web de Moodle."""
from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass

import httpx


def parse_token(raw: str) -> str:
    """Extrait un token Moodle de ce que l'utilisateur a collé : le token brut (32 hexadécimaux),
    ou le lien `moodlemobile://token=<base64>` renvoyé par la connexion mobile (SSO)."""
    raw = raw.strip().strip("\"'")
    if "token=" in raw:
        raw = raw.split("token=", 1)[1]
    if re.fullmatch(r"[0-9a-fA-F]{32}", raw):
        return raw.lower()
    try:
        decoded = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode()
        parts = decoded.split(":::")
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{32}", parts[1]):
            return parts[1].lower()
    except Exception:
        pass
    raise ValueError("Token Moodle non reconnu")


class MoodleError(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class MoodleAuthError(MoodleError):
    pass


@dataclass
class Quiz:
    id: int
    course_id: int
    course_name: str
    name: str
    url: str
    time_open: int  # timestamp Unix, 0 = pas de date d'ouverture
    time_close: int  # timestamp Unix, 0 = pas de date de fermeture
    time_limit: int  # secondes, 0 = illimité

    @property
    def status(self) -> str:
        now = time.time()
        if self.time_open and now < self.time_open:
            return "upcoming"
        if self.time_close and now > self.time_close:
            return "closed"
        return "open"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "course_id": self.course_id,
            "course_name": self.course_name,
            "name": self.name,
            "url": self.url,
            "time_open": self.time_open,
            "time_close": self.time_close,
            "time_limit": self.time_limit,
            "status": self.status,
        }


class MoodleClient:
    def __init__(self, base_url: str, service: str = "moodle_mobile_app",
                 http: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.service = service
        self.http = http or httpx.Client(timeout=30)

    def login(self, username: str, password: str) -> str:
        """Échange identifiant/mot de passe contre un token de service web."""
        r = self.http.post(
            f"{self.base_url}/login/token.php",
            data={"username": username, "password": password, "service": self.service},
        )
        r.raise_for_status()
        data = r.json()
        if "token" not in data:
            code = data.get("errorcode")
            msg = data.get("error", "Connexion Moodle impossible")
            if code in ("invalidlogin", "usernamenotfound"):
                raise MoodleAuthError(msg, code)
            raise MoodleError(msg, code)
        return data["token"]

    def _call(self, token: str, function: str, **params) -> dict | list:
        r = self.http.post(
            f"{self.base_url}/webservice/rest/server.php",
            data={"wstoken": token, "wsfunction": function,
                  "moodlewsrestformat": "json", **params},
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "exception" in data:
            code = data.get("errorcode")
            msg = data.get("message", data["exception"])
            if code == "invalidtoken":
                raise MoodleAuthError(msg, code)
            raise MoodleError(msg, code)
        return data

    def _calendar_quiz_ids(self, token: str) -> set[int]:
        """IDs de module (`coursemodule`) des QCM présents dans le calendrier. Pour ces
        événements, le champ `instance` est l'ID de module, pas l'ID du QCM."""
        since = int(time.time()) - 30 * 86400
        ids: set[int] = set()
        after = None
        while True:
            params = {"timesortfrom": since, "limitnum": 50}
            if after is not None:
                params["aftereventid"] = after
            res = self._call(token, "core_calendar_get_action_events_by_timesort", **params)
            events = res.get("events", [])
            ids.update(e["instance"] for e in events if e.get("modulename") == "quiz")
            if len(events) < 50:
                return ids
            after = res["lastid"]

    def site_info(self, token: str) -> dict:
        """Infos du compte lié au token (lève MoodleAuthError si le token est invalide)."""
        return self._call(token, "core_webservice_get_site_info")

    def get_quizzes(self, token: str) -> list[Quiz]:
        """QCM notés (note max > 0) présents dans le calendrier."""
        user_id = self._call(token, "core_webservice_get_site_info")["userid"]
        courses = self._call(token, "core_enrol_get_users_courses", userid=user_id)
        if not courses:
            return []
        in_calendar = self._calendar_quiz_ids(token)
        names = {c["id"]: c["fullname"] for c in courses}
        params = {f"courseids[{i}]": c["id"] for i, c in enumerate(courses)}
        res = self._call(token, "mod_quiz_get_quizzes_by_courses", **params)
        return [
            Quiz(
                id=q["id"],
                course_id=q["course"],
                course_name=names.get(q["course"], ""),
                name=q["name"],
                url=f"{self.base_url}/mod/quiz/view.php?id={q['coursemodule']}",
                time_open=q.get("timeopen", 0),
                time_close=q.get("timeclose", 0),
                time_limit=q.get("timelimit", 0),
            )
            for q in res.get("quizzes", [])
            if q["coursemodule"] in in_calendar and q.get("grade", 0) > 0
        ]
