import time

from cryptography.fernet import Fernet

from app.moodle import Quiz
from bot.notifier import pick_new
from bot.store import Store


def quiz(i, open_=0, close=0):
    return Quiz(i, 1, "Maths", f"Q{i}", "u", open_, close, 0)


def test_pick_new():
    now = int(time.time())
    qs = [quiz(1), quiz(2, open_=now + 600), quiz(3, close=now - 10), quiz(4)]
    send, skip = pick_new(qs, {1})
    assert [q.id for q in send] == [4]
    assert skip == {3}  # le QCM 2 (à venir) reste à traiter à son ouverture


def test_store_multi_user_and_encryption(tmp_path):
    path, key = str(tmp_path / "t.db"), Fernet.generate_key().decode()
    s = Store(path, key)
    s.upsert_user(1, 100, "https://a", "alice", "pw1")
    s.upsert_user(2, 200, "https://b", "bob", "pw2")
    assert {(u.id, u.channel_id, u.password) for u in s.active_users()} == {(1, 100, "pw1"), (2, 200, "pw2")}
    assert b"pw1" not in open(path, "rb").read()  # chiffré sur disque
    s.mark_notified(1, [5])
    s.upsert_user(1, 300, "https://a", "alice", "new")  # même compte : historique conservé, salon mis à jour
    assert s.notified(1) == {5} and s.notified(2) == set()
    u = [u for u in s.active_users() if u.id == 1][0]
    assert (u.channel_id, u.password) == (300, "new")
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
