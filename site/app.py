"""Pipeline: a small fake CRM used as a test bed for browser agents.

Chaos is injected from the run seed set by POST /reset?seed=N, and every
injection is written to the chaos_log table. That log is the ground truth the
divergence analysis uses to say *why* a run failed.
"""
import json
import os
import random
import sqlite3
import sys
import time
from collections import defaultdict

from flask import Flask, abort, g, jsonify, redirect, render_template, request, url_for

from seed import seed as seed_db

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
DB_PATH = os.environ.get("FLAKEBENCH_DB", os.path.join(HERE, "pipeline.db"))
PAGE_SIZE = 10
OWNERS = ["Ana", "Marco", "Riley", "Unassigned"]
STAGES = ["New", "Contacted", "Qualified", "Meeting", "Won", "Lost"]

# Probabilities per page load. Override with POST /reset?modal=0.5 etc.
DEFAULT_CHAOS = {"modal": 0.3, "reorder": 0.35, "newtab": 0.4, "slow_max_ms": 2500}

app = Flask(__name__)
STATE = {"seed": 0, "chaos": dict(DEFAULT_CHAOS), "enabled": True}
LOADS = defaultdict(int)


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def chaos_rng(page):
    """One RNG per (seed, page, nth load of that page): same seed, same chaos."""
    LOADS[page] += 1
    return random.Random(f"{STATE['seed']}:{page}:{LOADS[page]}")


def log_chaos(page, kind, **detail):
    db().execute("INSERT INTO chaos_log (ts, page, kind, detail) VALUES (?,?,?,?)",
                 (time.time(), page, kind, json.dumps(detail)))
    db().commit()


def page_chaos(page, rng, allow=("modal",)):
    """Decide this load's chaos. Returns a dict for the template."""
    c = {"modal_ms": None, "reorder_ms": None, "newtab": False, "slow_ms": 0}
    if not STATE["enabled"]:
        return c
    cfg = STATE["chaos"]
    if "modal" in allow and rng.random() < cfg["modal"]:
        c["modal_ms"] = rng.randint(400, 1800)
        log_chaos(page, "modal", delay_ms=c["modal_ms"])
    if "reorder" in allow and rng.random() < cfg["reorder"]:
        c["reorder_ms"] = rng.randint(600, 1600)
        log_chaos(page, "reorder", delay_ms=c["reorder_ms"])
    if "newtab" in allow and rng.random() < cfg["newtab"]:
        c["newtab"] = True
        log_chaos(page, "newtab")
    if "slow" in allow:
        c["slow_ms"] = rng.randint(200, int(cfg["slow_max_ms"]))
        log_chaos(page, "slow", delay_ms=c["slow_ms"])
    return c


# ---------------------------------------------------------------- control API

@app.post("/reset")
def reset():
    STATE["seed"] = int(request.values.get("seed", 0))
    STATE["enabled"] = request.values.get("chaos", "on") != "off"
    STATE["chaos"] = dict(DEFAULT_CHAOS)
    for k in DEFAULT_CHAOS:
        if k in request.values:
            STATE["chaos"][k] = float(request.values[k])
    LOADS.clear()
    close_db(None)
    seed_db(DB_PATH)
    return jsonify(ok=True, seed=STATE["seed"], chaos=STATE["chaos"], enabled=STATE["enabled"])


@app.get("/api/chaos")
def api_chaos():
    rows = db().execute("SELECT ts, page, kind, detail FROM chaos_log ORDER BY id").fetchall()
    return jsonify([dict(r) | {"detail": json.loads(r["detail"])} for r in rows])


# ---------------------------------------------------------------- leads

@app.get("/")
def home():
    return redirect(url_for("leads"))


@app.get("/leads")
def leads():
    page = max(1, int(request.args.get("page", 1)))
    stage = request.args.get("stage", "")
    where, params = ("WHERE stage = ?", [stage]) if stage else ("", [])
    total = db().execute(f"SELECT COUNT(*) FROM leads {where}", params).fetchone()[0]
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    rows = db().execute(
        f"SELECT * FROM leads {where} ORDER BY name LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
    c = page_chaos("leads", chaos_rng("leads"), allow=("modal", "reorder", "newtab"))
    return render_template("leads.html", rows=[dict(r) for r in rows], page=page, pages=pages,
                           total=total, stage=stage, stages=STAGES, chaos=c)


@app.route("/leads/<int:lead_id>", methods=["GET", "POST"])
def lead_detail(lead_id):
    lead = db().execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if lead is None:
        abort(404)
    if request.method == "POST":
        f = request.form
        db().execute("UPDATE leads SET stage = ?, owner = ?, notes = ?, last_activity = ? WHERE id = ?",
                     (f.get("stage", lead["stage"]), f.get("owner", lead["owner"]),
                      f.get("notes", lead["notes"]), "2026-11-25", lead_id))
        db().commit()
        return redirect(url_for("lead_detail", lead_id=lead_id, saved=1))
    c = page_chaos("lead_detail", chaos_rng("lead_detail"))
    return render_template("lead.html", lead=dict(lead), owners=OWNERS, stages=STAGES,
                           saved=request.args.get("saved"), chaos=c)


# ---------------------------------------------------------------- inbox

@app.get("/inbox")
def inbox():
    c = page_chaos("inbox", chaos_rng("inbox"), allow=("modal", "slow"))
    return render_template("inbox.html", chaos=c)


@app.get("/api/inbox")
def api_inbox():
    rows = db().execute("SELECT id, from_addr, subject, is_read, created_at FROM messages "
                        "WHERE folder = 'inbox' AND archived = 0 ORDER BY created_at DESC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/inbox/archive")
def inbox_archive():
    ids = [int(i) for i in request.form.getlist("ids")]
    if ids:
        db().execute(f"UPDATE messages SET archived = 1 WHERE id IN ({','.join('?' * len(ids))})", ids)
        db().commit()
    return redirect(url_for("inbox"))


@app.route("/inbox/<int:msg_id>", methods=["GET", "POST"])
def message(msg_id):
    m = db().execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
    if m is None:
        abort(404)
    if request.method == "POST":
        body = request.form.get("body", "").strip()
        if body:
            db().execute(
                "INSERT INTO messages (folder, from_addr, to_addr, subject, body, is_read, in_reply_to, created_at) "
                "VALUES ('sent', 'riley@pipeline.test', ?, ?, ?, 1, ?, '2026-11-25 12:00')",
                (m["from_addr"], "Re: " + m["subject"], body, msg_id))
            db().commit()
        return redirect(url_for("sent"))
    db().execute("UPDATE messages SET is_read = 1 WHERE id = ?", (msg_id,))
    db().commit()
    c = page_chaos("message", chaos_rng("message"))
    return render_template("message.html", m=dict(m), chaos=c)


@app.route("/compose", methods=["GET", "POST"])
def compose():
    if request.method == "POST":
        f = request.form
        if f.get("to") and f.get("subject"):
            db().execute(
                "INSERT INTO messages (folder, from_addr, to_addr, subject, body, is_read, created_at) "
                "VALUES ('sent', 'riley@pipeline.test', ?, ?, ?, 1, '2026-11-25 12:00')",
                (f["to"].strip(), f["subject"].strip(), f.get("body", "")))
            db().commit()
            return redirect(url_for("sent"))
    c = page_chaos("compose", chaos_rng("compose"))
    return render_template("compose.html", chaos=c, error=request.method == "POST")


@app.get("/sent")
def sent():
    rows = db().execute("SELECT * FROM messages WHERE folder = 'sent' ORDER BY id DESC").fetchall()
    return render_template("sent.html", rows=[dict(r) for r in rows], chaos=page_chaos("sent", chaos_rng("sent"), allow=()))


# ---------------------------------------------------------------- calendar

@app.route("/calendar", methods=["GET", "POST"])
def calendar():
    if request.method == "POST":
        f = request.form
        lead_id = int(f["lead_id"]) if f.get("lead_id") else None
        db().execute("INSERT INTO events (title, date, start_time, lead_id) VALUES (?,?,?,?)",
                     (f.get("title", "").strip(), f.get("date"), f.get("start_time"), lead_id))
        db().commit()
        return redirect(url_for("calendar"))
    events = db().execute("SELECT e.*, l.name AS lead_name FROM events e LEFT JOIN leads l ON l.id = e.lead_id "
                          "ORDER BY date, start_time").fetchall()
    leads_ = db().execute("SELECT id, name, company FROM leads ORDER BY name").fetchall()
    c = page_chaos("calendar", chaos_rng("calendar"))
    return render_template("calendar.html", events=[dict(e) for e in events],
                           leads=[dict(l) for l in leads_], chaos=c)


@app.route("/calendar/<int:event_id>", methods=["GET", "POST"])
def event_edit(event_id):
    e = db().execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if e is None:
        abort(404)
    if request.method == "POST":
        f = request.form
        db().execute("UPDATE events SET title = ?, date = ?, start_time = ? WHERE id = ?",
                     (f.get("title", e["title"]), f.get("date", e["date"]),
                      f.get("start_time", e["start_time"]), event_id))
        db().commit()
        return redirect(url_for("calendar"))
    c = page_chaos("event", chaos_rng("event"))
    return render_template("event.html", e=dict(e), chaos=c)


# ---------------------------------------------------------------- bring your own browser
#
# Any agent that drives its own browser can be graded without code changes:
#   POST /api/episode/start {"task": "t01_qualify_priya", "seed": 3, "agent": "my-agent"}
#       -> {"prompt": ..., "start_url": ...}; point your agent at start_url with the prompt
#   POST /api/episode/finish
#       -> {"passed": ..., "reason": ...}; a trace is written for the divergence analysis
# A small recorder in every page (templates/base.html) logs clicks, edits, page loads
# and tabs, so the trace comes from the site's side, not from the agent.

EPISODE = {"active": False}


def _tasks():
    from tasks import tasks as T
    f = os.environ.get("FLAKEBENCH_TASKS_FILE")
    if f and not getattr(T, "_user_loaded", False):
        T.load_tasks_file(f)
        T._user_loaded = True
    return T


@app.get("/api/tasks")
def api_tasks():
    return jsonify([{"id": t.id, "prompt": t.prompt, "tags": list(t.tags)} for t in _tasks().TASKS])


@app.post("/api/episode/start")
def episode_start():
    body = request.get_json(force=True, silent=True) or {}
    T = _tasks()
    task = T.BY_ID.get(body.get("task"))
    if task is None:
        return jsonify(error=f"unknown task {body.get('task')!r}", tasks=list(T.BY_ID)), 400
    seed = int(body.get("seed", 0))
    with app.test_request_context("/reset", method="POST",
                                  data={"seed": seed, "chaos": "on" if body.get("chaos", True) else "off"}):
        reset()
    EPISODE.clear()
    EPISODE.update(active=True, task=task.id, seed=seed, agent=body.get("agent", "external"),
                   out=body.get("out") or os.path.join(ROOT, "runs", "external-" + time.strftime("%Y%m%d")),
                   t0=time.time(), events=[], chaos=body.get("chaos", True))
    return jsonify(prompt=task.prompt, start_url=request.host_url.rstrip("/") + "/leads", task=task.id, seed=seed)


@app.post("/api/rec")
def api_rec():
    if EPISODE.get("active"):
        try:
            ev = json.loads(request.get_data(as_text=True) or "{}")
        except ValueError:
            ev = {}
        ev["t"] = time.time() - EPISODE["t0"]
        EPISODE["events"].append(ev)
    return ("", 204)


def _trace_from_events():
    tabs, steps = [], []
    for ev in EPISODE["events"]:
        tab = ev.get("tab", "?")
        if tab not in tabs:
            tabs.append(tab)
        if ev.get("type") not in ("click", "change"):
            continue
        kind = "click" if ev["type"] == "click" else ("select" if ev.get("tag") == "select" else "type")
        action = {"action": kind}
        if kind != "click":
            action["text" if kind == "type" else "option"] = ev.get("value", "")
        steps.append({
            "i": len(steps), "t_obs": ev["t"], "t_act": ev["t"], "think_s": None, "url": ev.get("url", ""),
            "dom_hash": "", "tabs": len(tabs), "active_tab": tabs.index(tab), "tabs_after": len(tabs),
            "new_tab": bool(ev.get("new_tab")), "modal_at_observe": bool(ev.get("modal")),
            "modal_at_act": bool(ev.get("modal")), "loading": bool(ev.get("loading")), "action": action,
            "target": {"tag": ev.get("tag"), "label": ev.get("label")},
            "reasoning": "", "result": None if ev.get("on_overlay") else "ok.",
            "error": "click landed on a blocking dialog (intercepts pointer events)" if ev.get("on_overlay") else None,
            "guards": [], "error_log": None, "screenshot": None,
        })
    steps.append({"i": len(steps), "t_obs": time.time() - EPISODE["t0"], "t_act": time.time() - EPISODE["t0"],
                  "think_s": None, "url": "", "dom_hash": "", "tabs": len(tabs), "active_tab": 0,
                  "tabs_after": len(tabs), "new_tab": False, "modal_at_observe": False, "modal_at_act": False,
                  "loading": False, "action": {"action": "done"}, "target": None, "reasoning": "", "result": "done",
                  "error": None, "guards": [], "error_log": None, "screenshot": None})
    return steps


@app.post("/api/episode/finish")
def episode_finish():
    if not EPISODE.get("active"):
        return jsonify(error="no episode running; POST /api/episode/start first"), 400
    T = _tasks()
    close_db(None)
    passed, reason = T.check(EPISODE["task"], DB_PATH)
    steps = _trace_from_events()
    chaos = [dict(r) | {"detail": json.loads(r["detail"]), "t": r["ts"] - EPISODE["t0"]}
             for r in db().execute("SELECT ts, page, kind, detail FROM chaos_log ORDER BY id").fetchall()]
    rec = {"task": EPISODE["task"], "seed": EPISODE["seed"], "passed": passed, "reason": reason,
           "n_steps": len(steps), "duration_s": round(time.time() - EPISODE["t0"], 1), "agent": EPISODE["agent"],
           "steps": steps, "chaos": chaos, "hit_step_limit": False, "source": "recorder"}
    out = EPISODE["out"]
    run_dir = os.path.join(out, rec["task"], f"run{rec['seed']}")
    os.makedirs(run_dir, exist_ok=True)
    json.dump(rec, open(os.path.join(run_dir, "trace.json"), "w"), indent=1)
    with open(os.path.join(out, "results.jsonl"), "a") as fh:
        fh.write(json.dumps({k: rec[k] for k in ("task", "seed", "passed", "reason", "n_steps", "duration_s")}) + "\n")
    meta = os.path.join(out, "meta.json")
    if not os.path.exists(meta):
        json.dump({"agent": EPISODE["agent"], "guards": [], "runs": None, "max_steps": None,
                   "chaos": EPISODE["chaos"], "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "source": "recorder"}, open(meta, "w"), indent=2)
    EPISODE["active"] = False
    return jsonify(passed=passed, reason=reason, steps=len(steps), trace=run_dir)


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        seed_db(DB_PATH)
    app.run(port=int(os.environ.get("PORT", 5055)), debug=False, threaded=False)
