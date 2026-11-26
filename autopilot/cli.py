"""./polarbench autopilot ...

  record RUN_DIR [--store workflows]         save the first passing run of each task as a workflow
  run [--store workflows] [--site-version 2] [--runs 5] [--healer gemini] [--out runs/x]
                                             replay every workflow against a fresh site, check
                                             the result with the task's checker, save healed versions
  list [--store workflows]                   workflows, their versions and recent runs
  show ID [--version N]                      print one workflow's steps
  rollback ID --to N                         make version N current again
  bench --agent gemini --healer gemini --runs 3 --out runs/bench-gemini
                                             the four rows (agent vs autopilot, old vs redesigned site)
                                             and a results page; resumable across days of free quota
  report BENCH_DIR                           rebuild that results page
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from autopilot.healers import make_healer  # noqa: E402
from autopilot.match import describe  # noqa: E402
from autopilot.record import record_run_dir  # noqa: E402
from autopilot.replay import Autopilot  # noqa: E402
from autopilot.store import Store  # noqa: E402

DEFAULT_STORE = os.path.join(ROOT, "workflows")


def cmd_record(a):
    store = Store(a.store)
    found = record_run_dir(a.run_dir, a.tasks.split(",") if a.tasks else None)
    for wf_id, wf in found.items():
        if store.index(wf_id)["versions"] and not a.force:
            print(f"{wf_id}: already saved (use --force to record a new version)")
            continue
        v = store.save(wf, f"Recorded from a passing run ({wf['source']['agent']}, seed {wf['source']['seed']}).")
        print(f"{wf_id}: v{v}, {len(wf['steps'])} steps (the agent took {wf['source']['agent_steps']})")
    if not found:
        print("no passing runs found in", a.run_dir)
    return found


def run_workflows(store, ids, site_version, runs, healer, fallback, out, chaos=True, port=5061,
                  save=True, heal_budget=8, log=print, seed0=0, resume=False):
    from agents.browser import ALL_GUARDS, BrowserEnv
    from runner.run import http, start_server
    from tasks.tasks import check
    os.makedirs(out, exist_ok=True)
    db_path = os.path.join(out, "pipeline.db")
    proc, base = start_server(db_path, port)
    env = BrowserEnv(base, guards=ALL_GUARDS)
    rows = []
    meta = {"kind": "autopilot", "site_version": site_version, "runs": runs, "chaos": chaos,
            "healer": getattr(healer, "name", None), "fallback": getattr(fallback, "name", None),
            "store": os.path.relpath(store.root, ROOT), "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    if not (resume and os.path.exists(os.path.join(out, "meta.json"))):
        json.dump(meta, open(os.path.join(out, "meta.json"), "w"), indent=2)
    try:
        with open(os.path.join(out, "results.jsonl"), "a") as fh:
            for wf_id in ids:
                for seed in range(seed0, seed0 + runs):
                    if resume and os.path.exists(os.path.join(out, wf_id, f"run{seed}", "replay.json")):
                        continue
                    wf = store.load(wf_id)
                    http("POST", base + "/reset", {"seed": seed, "chaos": "on" if chaos else "off",
                                                   "version": site_version})
                    run_dir = os.path.join(out, wf_id, f"run{seed}")
                    env.new_session(run_dir)
                    ap = Autopilot(env, healer=healer, fallback=fallback, heal_budget=heal_budget)
                    try:
                        res = ap.run(wf)
                    except Exception as e:
                        if type(e).__name__ == "RateLimited":
                            log(f"\nStopped: {e}\nRerun the same command later; finished runs are saved.")
                            raise
                        res = {"workflow": wf_id, "version": wf["version"], "status": "failed",
                               "reason": f"crash: {e!r}", "ai_calls": ap.ai_calls, "steps": [], "counts": {},
                               "verified": False, "fallback": None}
                    env.screenshot("final.jpg")
                    passed, reason = check(wf_id, db_path)
                    res.update(task=wf_id, seed=seed, site_version=site_version, passed=passed, check=reason)
                    if res.get("new_workflow") and save and passed is not None:
                        v = store.save(res.pop("new_workflow"), "; ".join(res["changes"]))
                        res["saved_version"] = v
                    res.pop("new_workflow", None)
                    json.dump(res, open(os.path.join(run_dir, "replay.json"), "w"), indent=1, default=str)
                    row = {k: res.get(k) for k in ("task", "seed", "site_version", "version", "passed", "verified",
                                                   "status", "ai_calls", "counts", "duration_s", "saved_version",
                                                   "fallback", "reason", "check")}
                    rows.append(row)
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
                    store.log_run(wf_id, dict(row, at=time.strftime("%Y-%m-%d %H:%M"), out=os.path.relpath(run_dir, ROOT)))
                    c = res.get("counts") or {}
                    log(f"{wf_id} run{seed} v{res['version']}: {'PASS' if passed else 'FAIL'}"
                        f"{' (verified)' if res.get('verified') else ''}  "
                        f"replayed {c.get('replayed', 0)}, auto-fixed {c.get('auto_fixed', 0)}, "
                        f"healed {c.get('healed', 0)}, AI calls {res['ai_calls']}"
                        f"{'  -> saved v' + str(res['saved_version']) if res.get('saved_version') else ''}"
                        f"{'' if passed else '  ' + (res.get('reason') or reason)}")
    finally:
        env.close()
        proc.terminate()
    return rows


def cmd_run(a):
    store = Store(a.store)
    ids = a.tasks.split(",") if a.tasks else store.ids()
    healer = make_healer(a.healer, a.heal_model, a.rpm)
    fallback = make_healer(a.fallback or a.healer, a.fallback_model or a.heal_model, a.rpm) if a.fallback != "none" else None
    out = os.path.abspath(a.out or os.path.join(ROOT, "runs", time.strftime("%Y%m%d-%H%M%S") + f"-autopilot-v{a.site_version}"))
    rows = run_workflows(store, ids, a.site_version, a.runs, healer, fallback, out, chaos=not a.no_chaos,
                         port=a.port, save=not a.no_save, heal_budget=a.heal_budget, seed0=a.seed0,
                         resume=a.resume)
    summarize(rows)
    print(f"\nresults in {out}")


def summarize(rows, label=""):
    if not rows:
        return {}
    n = len(rows)
    s = {"runs": n, "passed": sum(1 for r in rows if r["passed"]),
         "verified": sum(1 for r in rows if r["verified"]),
         "false_verified": sum(1 for r in rows if r["verified"] and not r["passed"]),
         "ai_calls": sum(r["ai_calls"] or 0 for r in rows),
         "zero_ai": sum(1 for r in rows if not r["ai_calls"]),
         "fallbacks": sum(1 for r in rows if r.get("fallback"))}
    print(f"{label}{s['passed']}/{n} passed, {s['verified']} verified ({s['false_verified']} verified but wrong), "
          f"{s['ai_calls']} AI calls total, {s['zero_ai']}/{n} runs used no AI, {s['fallbacks']} fell back to the agent")
    return s


def cmd_list(a):
    store = Store(a.store)
    for wf_id in store.ids():
        idx = store.index(wf_id)
        runs = store.runs(wf_id)
        last = runs[-1] if runs else None
        print(f"{wf_id:26s} v{idx['current']} of {len(idx['versions'])}  "
              + (f"last run: {'pass' if last['passed'] else 'fail'}, {last['ai_calls']} AI calls" if last else "never run"))


def cmd_show(a):
    wf = Store(a.store).load(a.id, a.version)
    print(f"{wf['id']} v{wf['version']}: {wf['goal']}\n{wf.get('changes', '')}\n")
    for n, s in enumerate(wf["steps"], 1):
        flags = " ".join(f for f, on in (("[nav]", s.get("nav")), ("[commit]", s.get("commit")),
                                         ("[needs OK]", s.get("confirm")), ("[AI]", s.get("healed"))) if on)
        print(f"{n:2d}. {describe(s)} {flags}")
    if wf.get("params"):
        print("\nparameters:", json.dumps(wf["params"]))


def cmd_rollback(a):
    Store(a.store).rollback(a.id, a.to)
    print(f"{a.id}: version {a.to} is current again")


def cmd_bench(a):
    try:
        _bench(a)
    except Exception as e:
        if type(e).__name__ != "RateLimited":
            raise
        print("Daily AI quota used up. Run the same command tomorrow; it carries on where it stopped.")


def _bench(a):
    """The four rows: agent every time vs autopilot, on the site as recorded and after the redesign."""
    from runner.run import main as run_main
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    store = Store(os.path.join(out, "workflows"))
    tasks = ["--tasks", a.tasks] if a.tasks else []
    agent_args = ["--agent", a.agent, "--runs", str(a.runs), "--guards", "all", "--resume", "--port", str(a.port)] + tasks
    if a.model:
        agent_args += ["--model", a.model]
    if a.rpm:
        agent_args += ["--rpm", str(a.rpm)]
    healer = make_healer(a.healer, a.heal_model or a.model, a.rpm)
    ids = lambda: [i for i in store.ids() if not a.tasks or i in a.tasks.split(",")]  # noqa: E731

    print("\n== 1/4  agent every time, site as recorded ==")
    if run_main(agent_args + ["--out", os.path.join(out, "agent-v1")]) == "rate_limited":
        return
    for wf_id, wf in record_run_dir(os.path.join(out, "agent-v1")).items():
        if not store.index(wf_id)["versions"]:
            store.save(wf, f"Recorded from a passing run ({wf['source']['agent']}, seed {wf['source']['seed']}).")
    print(f"recorded {len(store.ids())} workflows")
    print("\n== 2/4  autopilot, site as recorded ==")
    summarize(run_workflows(store, ids(), 1, a.runs, healer, healer, os.path.join(out, "autopilot-v1"),
                            port=a.port + 1, seed0=0, resume=True))
    print("\n== 3/4  agent every time, after the redesign ==")
    if run_main(agent_args + ["--site-version", "2", "--out", os.path.join(out, "agent-v2")]) == "rate_limited":
        return
    print("\n== 4/4  autopilot, after the redesign ==")
    summarize(run_workflows(store, ids(), 2, a.runs, healer, healer, os.path.join(out, "autopilot-v2"),
                            port=a.port + 2, seed0=0, resume=True))
    from autopilot.report import build
    print("\nreport:", build(out, os.path.join(out, "report"), note=a.note or ""))


def main(argv):
    ap = argparse.ArgumentParser(prog="polarbench autopilot", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("run_dir")
    r.add_argument("--tasks")
    r.add_argument("--force", action="store_true")
    x = sub.add_parser("run")
    x.add_argument("--tasks")
    x.add_argument("--site-version", type=int, default=1, choices=(1, 2))
    x.add_argument("--runs", type=int, default=5)
    x.add_argument("--seed0", type=int, default=1, help="first seed (seed 0 is usually the recorded run)")
    x.add_argument("--healer", default="none", help="none | scripted | gemini | ollama | claude | file.py:Class")
    x.add_argument("--heal-model")
    x.add_argument("--fallback", help="agent that finishes the task if healing fails (default: same as --healer; 'none' to disable)")
    x.add_argument("--fallback-model")
    x.add_argument("--heal-budget", type=int, default=8, help="max AI calls per run for healing")
    x.add_argument("--rpm", type=float)
    x.add_argument("--no-chaos", action="store_true")
    x.add_argument("--no-save", action="store_true", help="don't save healed versions")
    x.add_argument("--port", type=int, default=5061)
    x.add_argument("--out")
    x.add_argument("--resume", action="store_true", help="skip runs that already finished in --out")
    sub.add_parser("list")
    s = sub.add_parser("show")
    s.add_argument("id")
    s.add_argument("--version", type=int)
    b = sub.add_parser("rollback")
    b.add_argument("id")
    b.add_argument("--to", type=int, required=True)
    d = sub.add_parser("bench")
    d.add_argument("--agent", default="scripted")
    d.add_argument("--model")
    d.add_argument("--healer", default="scripted")
    d.add_argument("--heal-model")
    d.add_argument("--rpm", type=float)
    d.add_argument("--runs", type=int, default=3)
    d.add_argument("--tasks")
    d.add_argument("--note")
    d.add_argument("--port", type=int, default=5071)
    d.add_argument("--out", default=os.path.join(ROOT, "runs", "bench-scripted"))
    for p in (r, x, sub.choices["list"], s, b):
        p.add_argument("--store", default=DEFAULT_STORE)
    if argv and argv[0] == "report":
        from autopilot.report import main as report_main
        return report_main(argv[1:])
    a = ap.parse_args(argv)
    return {"record": cmd_record, "run": cmd_run, "list": cmd_list, "show": cmd_show,
            "rollback": cmd_rollback, "bench": cmd_bench}[a.cmd](a)


if __name__ == "__main__":
    main(sys.argv[1:])
