import httpx
from fastapi.testclient import TestClient

from app.main import app, get_client
from app.moodle import MoodleClient


def fake_moodle(request: httpx.Request) -> httpx.Response:
    form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
    if request.url.path.endswith("/login/token.php"):
        if form["password"] != "ok":
            return httpx.Response(200, json={"error": "Nom d'utilisateur ou mot de passe incorrect", "errorcode": "invalidlogin"})
        return httpx.Response(200, json={"token": "tok123"})
    fn = form["wsfunction"]
    if form["wstoken"] != "tok123":
        return httpx.Response(200, json={"exception": "moodle_exception", "errorcode": "invalidtoken", "message": "Token invalide"})
    if fn == "core_webservice_get_site_info":
        return httpx.Response(200, json={"userid": 7})
    if fn == "core_enrol_get_users_courses":
        return httpx.Response(200, json=[{"id": 1, "fullname": "Maths"}])
    if fn == "mod_quiz_get_quizzes_by_courses":
        return httpx.Response(200, json={"quizzes": [
            {"id": 10, "course": 1, "coursemodule": 55, "name": "QCM 1", "timeopen": 0, "timeclose": 0, "timelimit": 600, "grade": 20},
            {"id": 12, "course": 1, "coursemodule": 56, "name": "Non noté", "grade": 0},
            {"id": 13, "course": 1, "coursemodule": 57, "name": "Hors calendrier", "grade": 10}]})
    if fn == "core_calendar_get_action_events_by_timesort":
        return httpx.Response(200, json={"events": [
            {"modulename": "quiz", "instance": 55}, {"modulename": "assign", "instance": 11}], "lastid": 2})
    return httpx.Response(404)


def client():
    mc = MoodleClient("https://m.test", http=httpx.Client(transport=httpx.MockTransport(fake_moodle)))
    app.dependency_overrides[get_client] = lambda: mc
    return TestClient(app)


def test_login_and_quizzes():
    c = client()
    token = c.post("/login", json={"username": "u", "password": "ok"}).json()["token"]
    r = c.get("/quizzes", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert len(r.json()) == 1  # non noté et hors calendrier exclus
    assert r.json()[0] == {
        "id": 10, "course_id": 1, "course_name": "Maths", "name": "QCM 1",
        "url": "https://m.test/mod/quiz/view.php?id=55", "time_open": 0,
        "time_close": 0, "time_limit": 600, "status": "open"}


def test_bad_login():
    assert client().post("/login", json={"username": "u", "password": "no"}).status_code == 401


def test_bad_token():
    assert client().get("/quizzes", headers={"Authorization": "Bearer nope"}).status_code == 401
