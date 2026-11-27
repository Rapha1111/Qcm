import time

from cryptography.fernet import Fernet

from app.moodle import Quiz
from bot.notifier import pick_new, seed
from bot.store import Store


def quiz(i, open_=0, close=0):
    return Quiz(i, 1, "Maths", f"Q{i}", "u", open_, close, 0)


def test_pick_new():
    now = int(time.time())
    W = 1260
    qs = [quiz(1),                              # déjà notifié
          quiz(2, open_=now + 600),             # à venir -> surveillé
          quiz(3, close=now - 10),              # fermé -> ignoré
          quiz(4, open_=now - 60),              # vient de s'ouvrir -> notifié
          quiz(5, open_=now - 86400),           # ouvert depuis longtemps, découvert tard -> ignoré
          quiz(6, open_=now - 86400),           # ouvert depuis longtemps mais surveillé -> notifié
          quiz(7)]                              # sans date d'ouverture -> notifié
    plan = pick_new(qs, {1}, {6}, now, W)
    assert [q.id for q in plan.send] == [4, 6, 7]
    assert plan.skip == {3, 5} and plan.track == {2}


def test_seed_keeps_upcoming_notifiable():
    now = int(time.time())
    seen, up = seed([quiz(1), quiz(2, open_=now + 600), quiz(3, close=now - 1)])
    assert seen == {1, 3} and up == {2}


def test_parse_token():
    import base64, pytest
    from app.moodle import parse_token
    t = "a" * 32
    assert parse_token(t) == t and parse_token(f" {t.upper()} ") == t
    b64 = base64.b64encode(f"site:::{t}:::priv".encode()).decode()
    assert parse_token(f"moodlemobile://token={b64}") == t
    with pytest.raises(ValueError):
        parse_token("nimporte quoi")


def test_store_multi_user_and_encryption(tmp_path):
    path, key = str(tmp_path / "t.db"), Fernet.generate_key().decode()
    s = Store(path, key)
    s.upsert_user(1, 100, "https://a", "alice", "tok1")
    s.upsert_user(2, 200, "https://b", "bob", "tok2")
    assert {(u.id, u.channel_id, u.token) for u in s.active_users()} == {(1, 100, "tok1"), (2, 200, "tok2")}
    assert b"tok1" not in open(path, "rb").read()  # chiffré sur disque
    s.mark_notified(1, [5])
    s.upsert_user(1, 300, "https://a", "alice", "new")  # même compte : historique conservé, salon mis à jour
    assert s.notified(1) == {5} and s.notified(2) == set()
    u = [u for u in s.active_users() if u.id == 1][0]
    assert (u.channel_id, u.token) == (300, "new")
    s.upsert_user(1, 300, "https://a", "alice2", "x")  # autre compte : historique remis à zéro
    assert s.notified(1) == set()
    s.deactivate(2)
    assert [u.id for u in s.active_users()] == [1]
    assert s.delete_user(1) and not s.delete_user(1)
    s.upsert_user(3, 1, "https://a", "c", "p")
    s.mark_notified(3, [1])
    s.upsert_user(3, 1, "https://autre", "c", "p")  # autre Moodle : historique remis à zéro
    assert s.notified(3) == set()
    assert {u.id: u.moodle_url for u in s.active_users()}[3] == "https://autre"


def test_url_validation():
    import pytest
    from app.urlcheck import ensure_public_host, normalize_moodle_url
    assert normalize_moodle_url(" moodle.ecole.fr/ ") == "https://moodle.ecole.fr"
    assert normalize_moodle_url("https://x.fr/moodle/") == "https://x.fr/moodle"
    for bad in ("http://x.fr", "https://u:p@x.fr", "ftp://x.fr"):
        with pytest.raises(ValueError):
            normalize_moodle_url(bad)
    for host in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "[::1]"):
        with pytest.raises(ValueError):
            ensure_public_host(f"https://{host}")
