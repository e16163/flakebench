"""Eleven outreach tasks. Each checker reads the site's SQLite DB after a run and
returns (passed, reason). Checkers judge the final state only, never the path
the agent took, and they fail runs that changed things they shouldn't have.
"""
import sqlite3
from dataclasses import dataclass
from typing import Callable

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "site"))
from seed import _leads  # noqa: E402

SEED_LEADS = {r[0]: r for r in _leads(version=2)}  # a superset of version 1
# column index in a seed row
COL = {"stage": 5, "owner": 6, "notes": 8}


@dataclass
class Task:
    id: str
    prompt: str
    check: Callable[[sqlite3.Connection], tuple]
    tags: tuple = ()


def lead(con, name):
    return con.execute("SELECT * FROM leads WHERE name = ?", (name,)).fetchone()


def untouched_leads(con, allowed_ids):
    """Fail if any lead outside allowed_ids changed stage, owner or notes."""
    for row in con.execute("SELECT id, stage, owner, notes FROM leads"):
        if row["id"] in allowed_ids:
            continue
        s = SEED_LEADS[row["id"]]
        if (row["stage"], row["owner"], row["notes"]) != (s[COL["stage"]], s[COL["owner"]], s[COL["notes"]]):
            return False, f"side effect: lead {row['id']} ({s[1]}) was modified"
    return True, ""


def all_of(*checks):
    for ok, why in checks:
        if not ok:
            return False, why
    return True, "ok"


def sent(con):
    return con.execute("SELECT * FROM messages WHERE folder = 'sent'").fetchall()


# ---------------------------------------------------------------- checkers

def t01(con):
    r = lead(con, "Priya Raman")
    return all_of((r["stage"] == "Qualified", f"stage is {r['stage']}"),
                  untouched_leads(con, {r["id"]}))


def t02(con):
    rows = con.execute("SELECT * FROM leads WHERE company = 'Helix Robotics'").fetchall()
    wrong = [r["name"] for r in rows if r["owner"] != "Riley"]
    return all_of((not wrong, f"not assigned to Riley: {', '.join(wrong)}"),
                  untouched_leads(con, {r["id"] for r in rows}))


def t03(con):
    replies = [m for m in sent(con) if m["in_reply_to"] == 1]
    return all_of((len(replies) == 1, f"{len(replies)} replies to Dana's pricing email"),
                  (bool(replies) and "thursday" in replies[0]["body"].lower(), "reply does not mention Thursday"),
                  (len(sent(con)) == 1, "extra emails were sent"))


def t04(con):
    s = sent(con)
    ok = [m for m in s if m["to_addr"].lower() == "felix.wagner@quartz.com" and "intro" in m["subject"].lower()]
    wrong = [m for m in s if m["to_addr"].lower() == "felicity.wagner@quartz.com"]
    return all_of((not wrong, "emailed Felicity Wagner (wrong lead)"),
                  (len(ok) == 1, f"{len(ok)} intro emails to Felix Wagner"),
                  (len(s) == 1, "extra emails were sent"))


def t05(con):
    ev = con.execute("SELECT * FROM events WHERE title LIKE '%Arcadia%'").fetchall()
    rosa = lead(con, "Rosa Delgado")
    return all_of((len(ev) == 1, f"{len(ev)} Arcadia events"),
                  (bool(ev) and ev[0]["date"] == "2026-12-03", "wrong date"),
                  (bool(ev) and (ev[0]["start_time"] or "").startswith("14:00"), "wrong time"),
                  (bool(ev) and ev[0]["lead_id"] == rosa["id"], "event not linked to Rosa Delgado"))


def t06(con):
    rows = con.execute("SELECT id, from_addr, is_read, archived FROM messages WHERE folder = 'inbox'").fetchall()
    target = {r["id"] for r in rows if r["from_addr"].startswith("noreply@")}
    archived = {r["id"] for r in rows if r["archived"]}
    return all_of((target <= archived, f"newsletters still in inbox: {sorted(target - archived)}"),
                  (archived <= target, f"archived real mail: {sorted(archived - target)}"))


def t07(con):
    r = lead(con, "Tomás Ortega")
    return all_of((r["stage"] == "Lost", f"stage is {r['stage']}"),
                  ("budget frozen" in (r["notes"] or "").lower(), "note missing"),
                  untouched_leads(con, {r["id"]}))


def t08(con):
    r = lead(con, "Hugo Bianchi")  # Contacted lead with the oldest last activity
    return all_of((r["owner"] == "Riley", f"Hugo Bianchi owner is {r['owner']}"),
                  untouched_leads(con, {r["id"]}))


def t09(con):
    e = con.execute("SELECT * FROM events WHERE id = 1").fetchone()
    n = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    return all_of((e["date"] == "2026-12-04", f"event date is {e['date']}"),
                  (e["start_time"].startswith("10:00"), f"time changed to {e['start_time']}"),
                  (n == 3, "events were added instead of edited"))


def t10(con):
    r = lead(con, "Leo Strand")
    return all_of(("sent deck" in (r["notes"] or "").lower(), "note 'sent deck' missing on Leo Strand"),
                  untouched_leads(con, {r["id"]}))


def t11(con):
    want = {"Dana Whitfield", "Leo Strand", "Maya Iqbal"}  # people with unread mail
    rows = {r["name"]: r for r in con.execute("SELECT * FROM leads WHERE name IN (%s)" % ",".join("?" * 3), tuple(want))}
    missing = sorted(n for n, r in rows.items() if "follow up" not in (r["notes"] or "").lower())
    return all_of((not missing, f"note missing on: {', '.join(missing)}"),
                  untouched_leads(con, {r["id"] for r in rows.values()}))


TASKS = [
    Task("t01_qualify_priya", "Move the lead Priya Raman (Northwind Labs) to the Qualified stage.", t01, ("pagination",)),
    Task("t02_assign_helix", "Assign every lead from Helix Robotics to the owner Riley.", t02, ("pagination", "multi-item")),
    Task("t03_reply_pricing", "Reply to Dana's email about pricing for 40 seats. Say we can meet Thursday.", t03, ("slow-render",)),
    Task("t04_intro_quartz", "Email the VP Engineering at Quartz Health with a subject containing 'Intro'.", t04, ("lookup", "near-duplicate")),
    Task("t05_schedule_arcadia", "Create a calendar event titled 'Demo with Arcadia' on 2026-12-03 at 14:00, linked to the Arcadia Foods lead.", t05, ("form",)),
    Task("t06_archive_newsletters", "In the inbox, archive every automated newsletter or notification sent from a noreply address. Keep everything else.", t06, ("slow-render", "multi-item")),
    Task("t07_lose_tomas", "Mark the lead Tomás Ortega as Lost and add the note 'budget frozen'.", t07, ("pagination",)),
    Task("t08_oldest_contacted", "Among leads in the Contacted stage, find the one with the oldest last activity and assign it to Riley.", t08, ("reasoning", "reorder")),
    Task("t09_move_lumen", "Move the calendar event 'Sync with Lumen' from Dec 2 to Dec 4, keeping the same time.", t09, ("form",)),
    Task("t10_note_q1", "Someone emailed asking about the Q1 rollout. Add the note 'sent deck' to that person's lead record.", t10, ("cross-page",)),
    Task("t11_followups", "For every unread email from a real person (not an automated noreply sender), find that sender's lead and add the note 'follow up'.", t11, ("long-horizon", "cross-page", "multi-item")),
]

BY_ID = {t.id: t for t in TASKS}


def register(tasks):
    """Add your own tasks (see examples/my_tasks.py). Ids must be unique."""
    for t in tasks:
        if t.id in BY_ID:
            raise ValueError(f"duplicate task id {t.id}")
        TASKS.append(t)
        BY_ID[t.id] = t
    return tasks


def load_tasks_file(path):
    """Import a Python file that defines TASKS = [Task(...), ...] and register them."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"user_tasks_{abs(hash(path))}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return register(mod.TASKS)


def check(task_id, db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return BY_ID[task_id].check(con)
    except Exception as e:  # a crashed checker is a failed run, with the reason
        return False, f"checker error: {e!r}"
    finally:
        con.close()
