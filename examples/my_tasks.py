"""Add your own tasks. A task is an id, a prompt, and a checker that reads the
site's SQLite DB after the run and returns (passed, reason).

    ./polarbench run --tasks-file examples/my_tasks.py --tasks x01_unassign_birch

Tables: leads(id, name, company, email, title, stage, owner, last_activity, notes),
messages(id, folder, from_addr, to_addr, subject, body, is_read, archived, in_reply_to),
events(id, title, date, start_time, lead_id). Seed data: site/seed.py.
"""
from tasks.tasks import Task, all_of, untouched_leads


def check_unassign_birch(con):
    rows = con.execute("SELECT * FROM leads WHERE company = 'Birch Capital'").fetchall()
    wrong = [r["name"] for r in rows if r["owner"] != "Unassigned"]
    return all_of((not wrong, f"still assigned: {', '.join(wrong)}"),
                  untouched_leads(con, {r["id"] for r in rows}))


TASKS = [
    Task("x01_unassign_birch", "Set the owner of every Birch Capital lead to Unassigned.", check_unassign_birch,
         ("custom", "pagination", "multi-item")),
]
