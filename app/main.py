from __future__ import annotations

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from .moodle import MoodleAuthError, MoodleClient, MoodleError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    moodle_url: str = ""
    moodle_service: str = "moodle_mobile_app"


def get_client() -> MoodleClient:
    s = Settings()
    if not s.moodle_url:
        raise HTTPException(500, "MOODLE_URL non configuré (voir .env.example)")
    return MoodleClient(s.moodle_url, s.moodle_service)


class Credentials(BaseModel):
    username: str
    password: str


app = FastAPI(title="Moodle QCM API")


@app.exception_handler(MoodleAuthError)
async def _auth_error(_, exc: MoodleAuthError):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": str(exc)}, status_code=401)


@app.exception_handler(MoodleError)
async def _moodle_error(_, exc: MoodleError):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=502)


@app.exception_handler(httpx.HTTPError)
async def _http_error(_, exc: httpx.HTTPError):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": f"Moodle injoignable : {exc}"}, status_code=502)


@app.post("/login")
def login(creds: Credentials, client: MoodleClient = Depends(get_client)):
    """Renvoie un token Moodle à réutiliser sur /quizzes."""
    return {"token": client.login(creds.username, creds.password)}


@app.get("/quizzes")
def quizzes(authorization: str = Header(...), client: MoodleClient = Depends(get_client)):
    """Liste tous les QCM de tous tes cours. En-tête : Authorization: Bearer <token>."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "En-tête attendu : Authorization: Bearer <token>")
    return [q.to_dict() for q in client.get_quizzes(token)]
