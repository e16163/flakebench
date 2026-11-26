"""polarbench dashboard: start runs, watch them live, read results.

    ./polarbench ui            # http://localhost:8800

Everything is local. API keys typed into the form stay in this process's memory
and are passed only to the run you start; nothing is written to disk.
"""
import glob
import json
import os
import re
import socket
import subprocess
import sys
import time

from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, url_for

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from agents.browser import ALL_GUARDS  # noqa: E402
from report.build import CSS, FONT_LINK, FOOTER  # noqa: E402
from tasks.tasks import TASKS  # noqa: E402

RUNS = os.path.join(ROOT, "runs")
app = Flask(__name__)
app.jinja_env.globals.update(font_link=FONT_LINK, footer=FOOTER)
PROCS = {}  # run name -> Popen
KEYS = {}   # env var -> value, memory only

AGENTS = {
    "scripted": {"label": "Scripted baseline (free, no key)", "key": None, "model": ""},
    "gemini": {"label": "Gemini (free tier)", "key": "GEMINI_API_KEY", "model": "gemini-3.5-flash-lite"},
    "ollama": {"label": "Ollama (local, free)", "key": None, "model": "qwen3:8b"},
    "claude": {"label": "Claude (paid API)", "key": "ANTHROPIC_API_KEY", "model": "claude-haiku-5-5"},
}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def safe(name):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name or "") or not os.path.isdir(os.path.join(RUNS, name)):
        abort(404)
    return os.path.join(RUNS, name)


def read_results(d):
    p = os.path.join(d, "results.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p):
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def run_info(name):
    d = os.path.join(RUNS, name)
    meta = json.load(open(os.path.join(d, "meta.json"))) if os.path.exists(os.path.join(d, "meta.json")) else {}
    ui = json.load(open(os.path.join(d, "ui.json"))) if os.path.exists(os.path.join(d, "ui.json")) else {}
    res = read_results(d)
    proc = PROCS.get(name)
    running = proc is not None and proc.poll() is None
    total = ui.get("total") or len(res)
    passes = sum(r["passed"] for r in res)
    return {"name": name, "agent": meta.get("agent", ui.get("agent", "?")), "guards": meta.get("guards", []),
            "chaos": meta.get("chaos", True), "started": meta.get("started", ""), "done": len(res), "total": total,
            "passes": passes, "pass_rate": (passes / len(res)) if res else None, "running": running,
            "exit_code": None if proc is None or running else proc.returncode, "source": meta.get("source")}


def run_names():
    return sorted((os.path.basename(p) for p in glob.glob(os.path.join(RUNS, "*")) if os.path.isdir(p)
                   and not os.path.basename(p).startswith("_")),
                  key=lambda n: os.path.getmtime(os.path.join(RUNS, n)), reverse=True)


@app.context_processor
def sidebar():
    names = run_names()[:8]
    return {"side_runs": [{"name": n, "running": n in PROCS and PROCS[n].poll() is None} for n in names]}


@app.get("/")
def index():
    runs = [run_info(n) for n in run_names()]
    keys_set = {k: bool(os.environ.get(k) or KEYS.get(k)) for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY")}
    return render_template("index.html", css=CSS, active="new", runs=runs, tasks=TASKS, agents=AGENTS, guards=ALL_GUARDS,
                           keys_set=keys_set)


@app.post("/runs")
def start_run():
    f = request.form
    agent = f.get("agent", "scripted")
    if agent not in AGENTS:
        abort(400)
    key = AGENTS[agent]["key"]
    if key and f.get("api_key"):
        KEYS[key] = f["api_key"].strip()
    if key and not (os.environ.get(key) or KEYS.get(key)):
        return f"Missing {key}. Go back and paste a key, or export it before ./polarbench ui.", 400
    tasks = f.getlist("tasks") or [t.id for t in TASKS]
    guards = [g for g in f.getlist("guards") if g in ALL_GUARDS]
    runs = max(1, min(20, int(f.get("runs", 5))))
    model = (f.get("model") or "").strip()
    gtag = "noguards" if not guards else ("allguards" if len(guards) == len(ALL_GUARDS) else "_".join(guards))
    name = f"{agent}-{gtag}{'-nochaos' if f.get('no_chaos') else ''}-{time.strftime('%m%d-%H%M%S')}"
    out = os.path.join(RUNS, name)
    os.makedirs(out, exist_ok=True)
    cmd = [sys.executable, os.path.join(ROOT, "runner", "run.py"), "--agent", agent, "--runs", str(runs),
           "--tasks", ",".join(tasks), "--out", out, "--port", str(free_port())]
    if guards:
        cmd += ["--guards", ",".join(guards)]
    if model and agent != "scripted":
        cmd += ["--model", model]
    if agent == "claude":
        cmd += ["--compact"]
    if f.get("no_chaos"):
        cmd += ["--no-chaos"]
    json.dump({"total": runs * len(tasks), "tasks": tasks, "runs": runs, "agent": agent, "cmd": cmd[1:]},
              open(os.path.join(out, "ui.json"), "w"), indent=2)
    env = dict(os.environ, **KEYS, PYTHONUNBUFFERED="1")
    PROCS[name] = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=open(os.path.join(out, "run.log"), "w"),
                                   stderr=subprocess.STDOUT)
    return redirect(url_for("live", name=name))


@app.post("/runs/<name>/stop")
def stop_run(name):
    safe(name)
    p = PROCS.get(name)
    if p and p.poll() is None:
        p.terminate()
    return redirect(url_for("live", name=name))


@app.get("/runs/<name>")
def live(name):
    d = safe(name)
    ui = json.load(open(os.path.join(d, "ui.json"))) if os.path.exists(os.path.join(d, "ui.json")) else {}
    tasks = ui.get("tasks") or sorted({r["task"] for r in read_results(d)})
    seeds = ui.get("runs") or (max([r["seed"] for r in read_results(d)] or [0]) + 1)
    prompts = {t.id: t.prompt for t in TASKS}
    return render_template("run.html", css=CSS, active="runs", name=name, info=run_info(name), tasks=tasks, seeds=seeds,
                           prompts=prompts, cmd=_pretty_cmd(ui.get("cmd", [])) if ui else "")


def _pretty_cmd(cmd):
    """The equivalent terminal command, minus the auto-picked port."""
    args, out, skip = cmd[1:], [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--port":
            skip = True
            continue
        out.append(os.path.relpath(a, ROOT) if a.startswith(ROOT) else a)
    return " ".join(["./polarbench run"] + out)


@app.get("/api/runs/<name>")
def api_run(name):
    d = safe(name)
    shots = glob.glob(os.path.join(d, "*", "run*", "step_*.*"))
    latest = max(shots, key=os.path.getmtime) if shots else None
    log = os.path.join(d, "run.log")
    tail = open(log).read().splitlines()[-12:] if os.path.exists(log) else []
    return jsonify(info=run_info(name), results=read_results(d),
                   latest=os.path.relpath(latest, d) if latest else None,
                   latest_label=("/".join(os.path.relpath(latest, d).split(os.sep)[:2]) if latest else None), log=tail)


@app.get("/runs/<name>/file/<path:p>")
def run_file(name, p):
    return send_from_directory(safe(name), p)


def _reference_for(name):
    """A finished no-chaos run of the same agent makes divergence sharper; use the newest one."""
    info = run_info(name)
    for n in sorted(os.listdir(RUNS), reverse=True):
        if n != name and os.path.isdir(os.path.join(RUNS, n)):
            o = run_info(n)
            if o["agent"] == info["agent"] and not o["chaos"] and o["done"]:
                return os.path.join(RUNS, n)
    return None


@app.get("/runs/<name>/report/")
def report(name):
    d = safe(name)
    if not read_results(d):
        return "No finished runs yet.", 404
    from report.build import build
    build(d, _reference_for(name))
    return send_from_directory(os.path.join(d, "report"), "index.html")


@app.get("/runs/<name>/report/<path:p>")
def report_file(name, p):
    return send_from_directory(os.path.join(safe(name), "report"), p)


@app.get("/compare")
def compare():
    names = request.args.getlist("r")
    dirs = [safe(n) for n in names]
    if len(dirs) < 2:
        return redirect(url_for("index"))
    out = os.path.join(RUNS, "_compare", re.sub(r"[^A-Za-z0-9]+", "_", "_".join(names))[:120])
    subprocess.check_call([sys.executable, os.path.join(ROOT, "report", "compare.py"), *dirs, "--out", out], cwd=ROOT)
    return redirect(url_for("compare_file", key=os.path.basename(out), p="index.html"))


@app.get("/compare/<key>/<path:p>")
def compare_file(key, p):
    if not re.fullmatch(r"[A-Za-z0-9_]+", key):
        abort(404)
    return send_from_directory(os.path.join(RUNS, "_compare", key), p)


def serve(port=8800):
    os.makedirs(RUNS, exist_ok=True)
    print(f"polarbench dashboard: http://localhost:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    serve()
