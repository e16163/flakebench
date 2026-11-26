"""Workflows view: saved workflows on autopilot, their versions, and each run step by step."""
import json
import os
import re
import subprocess
import sys
import time

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for

from autopilot.match import describe
from autopilot.record import record_run_dir
from autopilot.store import Store

bp = Blueprint("workflows", __name__)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RUNS = os.path.join(ROOT, "runs")
HEALERS = {
    "none": ("No AI (replay and free fixes only)", None),
    "gemini": ("Gemini, free tier", "GEMINI_API_KEY"),
    "claude": ("Claude Haiku (paid API)", "ANTHROPIC_API_KEY"),
    "scripted": ("Scripted stand-in (no key; for trying the flow)", None),
}
WF_PROCS = {}


def store():
    return Store(current_app.config.get("WF_STORE") or os.environ.get("POLARBENCH_WORKFLOWS")
                 or os.path.join(ROOT, "workflows"))


def _id(wf_id):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", wf_id or "") or wf_id not in store().ids():
        abort(404)
    return wf_id


def _render(tpl, **kw):
    from report.build import CSS
    return render_template(tpl, css=CSS, active="workflows", **kw)


def _recordable():
    out = []
    for n in sorted(os.listdir(RUNS), reverse=True) if os.path.isdir(RUNS) else []:
        p = os.path.join(RUNS, n, "results.jsonl")
        if n.startswith("_") or not os.path.exists(p):
            continue
        meta = os.path.join(RUNS, n, "meta.json")
        if os.path.exists(meta) and json.load(open(meta)).get("kind") == "autopilot":
            continue
        rows = [json.loads(l) for l in open(p) if l.strip()]
        passed = {r["task"] for r in rows if r.get("passed")}
        if passed:
            out.append({"name": n, "tasks": len(passed)})
    return out


def _summary(wf_id):
    s = store()
    idx = s.index(wf_id)
    wf = s.load(wf_id)
    runs = s.runs(wf_id)
    return {"id": wf_id, "goal": wf["goal"], "version": idx["current"], "n_versions": len(idx["versions"]),
            "steps": len(wf["steps"]), "agent_steps": wf["source"].get("agent_steps"),
            "last": runs[-1] if runs else None, "n_runs": len(runs),
            "ai_free": sum(1 for r in runs if not r.get("ai_calls")),
            "needs_ok": any(st.get("confirm") for st in wf["steps"])}


@bp.get("/workflows")
def workflows():
    s = store()
    items = [_summary(i) for i in s.ids()]
    return _render("workflows.html", items=items, recordable=_recordable(), healers=HEALERS)


@bp.post("/workflows/record")
def record():
    name = request.form.get("run", "")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or not os.path.isdir(os.path.join(RUNS, name)):
        abort(400)
    s = store()
    for wf_id, wf in record_run_dir(os.path.join(RUNS, name)).items():
        if not s.index(wf_id)["versions"]:
            s.save(wf, f"Recorded from a passing run ({wf['source']['agent']}, seed {wf['source']['seed']}).")
    return redirect(url_for("workflows.workflows"))


@bp.get("/workflows/<wf_id>")
def workflow(wf_id):
    s = store()
    wf_id = _id(wf_id)
    idx = s.index(wf_id)
    v = request.args.get("v", type=int) or idx["current"]
    wf = s.load(wf_id, v)
    steps = [dict(st, desc=describe(st), value=st.get("text")) for st in wf["steps"]]
    runs = list(reversed(s.runs(wf_id)))[:20]
    for r in runs:  # out is runs/<dir...>/<id>/<runN>
        parts = (r.get("out") or "").split("/")
        if len(parts) >= 4 and parts[0] == "runs":
            r["link"] = f"/workflows/{wf_id}/runs/{'/'.join(parts[1:-2])}/{parts[-1]}"
    return _render("workflow.html", wf=wf, idx=idx, steps=steps, runs=runs, shown=v, healers=HEALERS,
                   summary=_summary(wf_id))


@bp.post("/workflows/<wf_id>/rollback")
def rollback(wf_id):
    wf_id = _id(wf_id)
    store().rollback(wf_id, int(request.form["to"]))
    return redirect(url_for("workflows.workflow", wf_id=wf_id))


@bp.post("/workflows/<wf_id>/run")
def run(wf_id):
    from ui.app import KEYS, free_port
    wf_id = _id(wf_id)
    f = request.form
    healer = f.get("healer", "none")
    if healer not in HEALERS:
        abort(400)
    key = HEALERS[healer][1]
    if key and f.get("api_key"):
        KEYS[key] = f["api_key"].strip()
    if key and not (os.environ.get(key) or KEYS.get(key)):
        return f"{HEALERS[healer][0]} needs {key}. Go back and paste a key, or export it before ./polarbench ui.", 400
    site_version = 2 if f.get("site_version") == "2" else 1
    seed = len(store().runs(wf_id)) + 1
    name = f"wf-{wf_id}-{time.strftime('%m%d-%H%M%S')}"
    out = os.path.join(RUNS, name)
    os.makedirs(out, exist_ok=True)
    cmd = [sys.executable, os.path.join(ROOT, "polarbench"), "autopilot", "run", "--tasks", wf_id, "--runs", "1",
           "--seed0", str(seed), "--site-version", str(site_version), "--healer", healer, "--out", out,
           "--port", str(free_port()), "--store", store().root]
    env = dict(os.environ, **KEYS, PYTHONUNBUFFERED="1")
    WF_PROCS[name] = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=open(os.path.join(out, "run.log"), "w"),
                                      stderr=subprocess.STDOUT)
    return redirect(url_for("workflows.run_view", wf_id=wf_id, out=name, run=f"run{seed}"))


@bp.get("/workflows/<wf_id>/runs/<path:out>/<run>")
def run_view(wf_id, out, run):
    wf_id = _id(wf_id)
    if not re.fullmatch(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*", out) or ".." in out or not re.fullmatch(r"run\d+", run):
        abort(404)
    d = os.path.join(RUNS, out, wf_id, run)
    p = os.path.join(d, "replay.json")
    res = json.load(open(p)) if os.path.exists(p) else None
    proc = WF_PROCS.get(out)
    running = res is None and proc is not None and proc.poll() is None
    log = os.path.join(RUNS, out, "run.log")
    tail = open(log).read().splitlines()[-8:] if os.path.exists(log) else []
    head, _, rest = out.partition("/")
    shot = f"/runs/{head}/file/{rest + '/' if rest else ''}{wf_id}/{run}/final.jpg" if os.path.exists(os.path.join(d, "final.jpg")) else None
    return _render("wf_run.html", wf_id=wf_id, res=res, running=running, tail=tail, shot=shot, out=out, run=run,
                   summary=_summary(wf_id))
