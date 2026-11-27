import time

from app.moodle import Quiz
from bot.notifier import State, find_new


def quiz(i, open_=0, close=0):
    return Quiz(i, 1, "Maths", f"Q{i}", "u", open_, close, 0)


def test_flow(tmp_path):
    now = int(time.time())
    p = tmp_path / "s.json"
    s = State(p)
    assert find_new([quiz(1)], s) == []  # 1er lancement : on amorce sans notifier
    s = State(p)
    soon = quiz(2, open_=now + 600)
    closed = quiz(3, close=now - 10)
    assert find_new([quiz(1), soon, closed, quiz(4)], s) == [quiz(4)]
    soon.time_open = now - 1  # il vient de s'ouvrir
    assert find_new([quiz(1), soon, closed, quiz(4)], State(p)) == [soon]
    assert find_new([quiz(1), soon, closed, quiz(4)], State(p)) == []
