"""Every checker must fail on the untouched seed and catch side effects."""
import sqlite3

import pytest

from seed import seed
from tasks.tasks import TASKS, check


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "p.db")
    seed(p)
    return p


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_no_task_passes_on_seed(db, task):
    ok, why = check(task.id, db)
    assert not ok, f"{task.id} passes without doing anything"


def test_t01_passes_when_done(db):
    con = sqlite3.connect(db)
    con.execute("UPDATE leads SET stage='Qualified' WHERE name='Priya Raman'")
    con.commit()
    assert check("t01_qualify_priya", db) == (True, "ok")


def test_side_effect_fails_run(db):
    con = sqlite3.connect(db)
    con.execute("UPDATE leads SET stage='Qualified' WHERE name='Priya Raman'")
    con.execute("UPDATE leads SET owner='Riley' WHERE name='Grace Liu'")
    con.commit()
    ok, why = check("t01_qualify_priya", db)
    assert not ok and "side effect" in why


def test_t04_near_duplicate_trap(db):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO messages (folder, from_addr, to_addr, subject, body) "
                "VALUES ('sent', 'riley@pipeline.test', 'felicity.wagner@quartz.com', 'Intro', '')")
    con.commit()
    ok, why = check("t04_intro_quartz", db)
    assert not ok and "Felicity" in why


def test_t08_answer_is_unique(db):
    con = sqlite3.connect(db)
    rows = con.execute("SELECT name, last_activity FROM leads WHERE stage='Contacted' ORDER BY last_activity").fetchall()
    assert rows[0][0] == "Hugo Bianchi" and rows[0][1] < rows[1][1]


def test_custom_tasks_file(db):
    import os
    from tasks import tasks as T
    if "x01_unassign_birch" not in T.BY_ID:
        T.load_tasks_file(os.path.join(os.path.dirname(__file__), "..", "examples", "my_tasks.py"))
    ok, why = T.check("x01_unassign_birch", db)
    assert not ok and "still assigned" in why
