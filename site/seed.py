"""Deterministic seed data for the Pipeline CRM.

The data never changes between runs. Only the chaos (modal timing, reorders,
new-tab links, slow inbox) depends on the run seed, so every run of a task
starts from the same facts.
"""
import random
import sqlite3

SCHEMA = """
DROP TABLE IF EXISTS leads;
DROP TABLE IF EXISTS messages;
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS chaos_log;
DROP TABLE IF EXISTS meta;
CREATE TABLE leads (
  id INTEGER PRIMARY KEY, name TEXT, company TEXT, email TEXT, title TEXT,
  stage TEXT, owner TEXT, last_activity TEXT, notes TEXT DEFAULT ''
);
CREATE TABLE messages (
  id INTEGER PRIMARY KEY, folder TEXT, from_addr TEXT, to_addr TEXT,
  subject TEXT, body TEXT, is_read INTEGER DEFAULT 0, archived INTEGER DEFAULT 0,
  in_reply_to INTEGER, created_at TEXT
);
CREATE TABLE events (
  id INTEGER PRIMARY KEY, title TEXT, date TEXT, start_time TEXT, lead_id INTEGER
);
CREATE TABLE chaos_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, page TEXT, kind TEXT, detail TEXT
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

FIRST = ["Ava", "Noah", "Mia", "Liam", "Zoe", "Ethan", "Isla", "Kai", "Lena", "Omar",
         "Ruth", "Sam", "Tara", "Vik", "Wen", "Yara", "Jon", "Hana", "Ivan", "Nia"]
LAST = ["Chen", "Patel", "Brooks", "Kim", "Okafor", "Silva", "Novak", "Haas", "Reyes",
        "Lund", "Moreau", "Ito", "Farah", "Quinn", "Duval", "Stone", "Berg", "Cruz"]
COMPANIES = ["Northwind Labs", "Helix Robotics", "Quartz Health", "Arcadia Foods",
             "Cobalt Freight", "Lumen Analytics", "Birch Capital", "Ferro Systems",
             "Juniper Bio", "Orbit Retail", "Sable Security", "Tidal Energy"]
TITLES = ["Head of Growth", "CTO", "VP Engineering", "Ops Manager", "Founder",
          "Director of Sales", "Data Lead", "Product Manager"]
STAGES = ["New", "Contacted", "Qualified", "Meeting", "Won", "Lost"]
OWNERS = ["Ana", "Marco", "Unassigned"]

# Leads that tasks depend on. Placed by id so they land on pages 2-5 when
# the list is sorted by name.
FIXED = {
    7:  ("Priya Raman", "Northwind Labs", "Head of Growth", "Contacted", "Ana", "2026-11-18"),
    12: ("Tomás Ortega", "Birch Capital", "Founder", "Meeting", "Marco", "2026-11-20"),
    15: ("Grace Liu", "Helix Robotics", "CTO", "New", "Unassigned", "2026-11-10"),
    23: ("Dev Malhotra", "Helix Robotics", "Data Lead", "Contacted", "Ana", "2026-11-12"),
    38: ("Uma Sorensen", "Helix Robotics", "Ops Manager", "Qualified", "Marco", "2026-11-21"),
    19: ("Felix Wagner", "Quartz Health", "VP Engineering", "Qualified", "Ana", "2026-11-19"),
    20: ("Felicity Wagner", "Quartz Health", "Product Manager", "New", "Marco", "2026-11-22"),
    27: ("Rosa Delgado", "Arcadia Foods", "Director of Sales", "Meeting", "Ana", "2026-11-23"),
    31: ("Dana Whitfield", "Cobalt Freight", "Ops Manager", "Qualified", "Marco", "2026-11-24"),
    34: ("Leo Strand", "Lumen Analytics", "Founder", "Meeting", "Ana", "2026-11-17"),
    41: ("Hugo Bianchi", "Tidal Energy", "CTO", "Contacted", "Marco", "2026-09-02"),
    44: ("Maya Iqbal", "Juniper Bio", "Head of Growth", "Contacted", "Ana", "2026-10-15"),
}

N_LEADS = 47

# Added in site version 2. Their names sort first, so every other lead moves
# down six rows and lands on a different page than it did in version 1.
V2_LEADS = [
    (48, "Aaron Holt", "Orbit Retail", "Founder", "New", "Ana", "2026-11-25"),
    (49, "Abby Marsh", "Ferro Systems", "CTO", "New", "Marco", "2026-11-25"),
    (50, "Adele Fox", "Sable Security", "Data Lead", "New", "Ana", "2026-11-25"),
    (51, "Ahmed Rana", "Tidal Energy", "Ops Manager", "New", "Marco", "2026-11-25"),
    (52, "Alice Ng", "Birch Capital", "Product Manager", "New", "Ana", "2026-11-25"),
    (53, "Amir Saleh", "Juniper Bio", "Head of Growth", "New", "Marco", "2026-11-25"),
]


def _email(name, company):
    return name.lower().replace(" ", ".").replace("á", "a") + "@" + company.lower().split()[0] + ".com"


def _leads(version=1):
    rng = random.Random(42)
    rows = []
    used = {v[0] for v in FIXED.values()}
    for i in range(1, N_LEADS + 1):
        if i in FIXED:
            name, company, title, stage, owner, last = FIXED[i]
        else:
            while True:
                name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
                if name not in used:
                    used.add(name)
                    break
            # keep Helix / Quartz / Arcadia leads limited to the fixed ones
            company = rng.choice([c for c in COMPANIES if c not in
                                  ("Helix Robotics", "Quartz Health", "Arcadia Foods")])
            title = rng.choice(TITLES)
            stage = rng.choice(["New", "Qualified", "Meeting", "Won", "Lost"])
            owner = rng.choice(OWNERS)
            last = f"2026-11-{rng.randint(1, 24):02d}"
        rows.append((i, name, company, _email(name, company), title, stage, owner, last, ""))
    if version == 2:
        for i, name, company, title, stage, owner, last in V2_LEADS:
            rows.append((i, name, company, _email(name, company), title, stage, owner, last, ""))
    return rows


MESSAGES = [
    # id, folder, from, to, subject, body, is_read, created_at
    (1, "inbox", "dana.whitfield@cobalt.com", "riley@pipeline.test", "Pricing for 40 seats",
     "Hi Riley, can you send pricing for 40 seats? We'd like to talk this week.", 0, "2026-11-24 09:12"),
    (2, "inbox", "noreply@growthweekly.io", "riley@pipeline.test", "Growth Weekly #212",
     "This week: 9 cold email templates.", 0, "2026-11-24 07:00"),
    (3, "inbox", "leo.strand@lumen.com", "riley@pipeline.test", "Q1 rollout",
     "We're planning the Q1 rollout. Can you share the deck?", 0, "2026-11-23 16:40"),
    (4, "inbox", "noreply@saasdigest.com", "riley@pipeline.test", "SaaS Digest: November",
     "Top funding rounds this month.", 0, "2026-11-23 06:00"),
    (5, "inbox", "rosa.delgado@arcadia.com", "riley@pipeline.test", "Re: demo",
     "Early December works for a demo.", 1, "2026-11-22 11:05"),
    (6, "inbox", "noreply@growthweekly.io", "riley@pipeline.test", "Growth Weekly #211",
     "Last week: pipeline hygiene.", 1, "2026-11-17 07:00"),
    (7, "inbox", "maya.iqbal@juniper.com", "riley@pipeline.test", "Checking in",
     "Any update on the proposal?", 0, "2026-11-21 13:30"),
    (8, "inbox", "noreply@eventsbot.com", "riley@pipeline.test", "Webinar reminder",
     "Your webinar starts tomorrow.", 0, "2026-11-21 08:00"),
]

EVENTS = [
    (1, "Sync with Lumen", "2026-12-02", "10:00", 34),
    (2, "Pipeline review", "2026-12-01", "09:00", None),
    (3, "Call with Birch", "2026-12-03", "11:30", 12),
]


def seed(db_path, version=1):
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO leads VALUES (?,?,?,?,?,?,?,?,?)", _leads(version))
    con.executemany(
        "INSERT INTO messages (id, folder, from_addr, to_addr, subject, body, is_read, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)", MESSAGES)
    con.executemany("INSERT INTO events VALUES (?,?,?,?,?)", EVENTS)
    con.commit()
    con.close()


if __name__ == "__main__":
    import sys
    seed(sys.argv[1] if len(sys.argv) > 1 else "pipeline.db")
