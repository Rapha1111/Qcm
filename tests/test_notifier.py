import time

from cryptography.fernet import Fernet

from app.moodle import Quiz, parse_token
from bot.store import Store


def quiz(i, open_=0, close=0):
    return Quiz(i, 1, "Maths", f"Q{i}", "u", open_, close, 0)


def test_parse_token():
    import base64, pytest
    t = "a" * 32
    assert parse_token(t) == t and parse_token(f" {t.upper()} ") == t
    b64 = base64.b64encode(f"site:::{t}:::priv".encode()).decode()
    assert parse_token(f"moodlemobile://token={b64}") == t
    with pytest.raises(ValueError):
        parse_token("nimporte quoi")


def test_url_validation():
    import pytest
    from app.urlcheck import ensure_public_host, normalize_moodle_url
    assert normalize_moodle_url(" moodle.ecole.fr/ ") == "https://moodle.ecole.fr"
    for bad in ("http://x.fr", "https://u:p@x.fr", "ftp://x.fr"):
        with pytest.raises(ValueError):
            normalize_moodle_url(bad)
    for host in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "[::1]"):
        with pytest.raises(ValueError):
            ensure_public_host(f"https://{host}")


def test_store(tmp_path):
    path, key = str(tmp_path / "t.db"), Fernet.generate_key().decode()
    s = Store(path, key)
    s.upsert_guild(1, 100, 9, "https://a", "alice", "tok1")
    s.upsert_guild(2, 200, 9, "https://b", "bob", "tok2")
    assert b"tok1" not in open(path, "rb").read()  # chiffré sur disque
    assert s.get_guild(1).token == "tok1" and len(s.active_guilds()) == 2

    now = int(time.time())
    s.replace_quizzes(1, [quiz(1, open_=now + 600), quiz(2, open_=now - 5, close=now + 99)])
    s.replace_quizzes(2, [quiz(1, open_=now + 600)])
    assert [(g, q.id) for g, q in s.due(now)] == [(1, 2)]  # seul celui dont l'heure est passée
    s.delete_quiz(1, 2)
    assert s.due(now) == [] and s.count_quizzes(1) == 1

    s.replace_quizzes(1, [quiz(7, open_=now + 5)])  # le remplacement purge les anciens
    assert s.count_quizzes(1) == 1 and s.count_quizzes(2) == 1
    s.upsert_guild(1, 300, 9, "https://a", "alice", "new")  # même compte : QCM conservés
    assert s.count_quizzes(1) == 1 and s.get_guild(1).channel_id == 300
    s.upsert_guild(1, 300, 9, "https://a", "autre", "x")  # autre compte : QCM effacés
    assert s.count_quizzes(1) == 0
    s.deactivate(2)
    assert [g.id for g in s.active_guilds()] == [1]
    assert s.delete_guild(1) and not s.delete_guild(1)
