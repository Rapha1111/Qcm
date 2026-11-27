"""Client minimal pour l'API de services web de Moodle."""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


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

    def get_quizzes(self, token: str) -> list[Quiz]:
        user_id = self._call(token, "core_webservice_get_site_info")["userid"]
        courses = self._call(token, "core_enrol_get_users_courses", userid=user_id)
        if not courses:
            return []
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
        ]
