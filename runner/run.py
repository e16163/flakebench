"""Run every task N times against a freshly reset site and log full traces.

    python runner/run.py --agent scripted              # no API key needed
    python runner/run.py --agent claude --runs 5       # needs ANTHROPIC_API_KEY
    python runner/run.py --agent claude --tasks t01_qualify_priya,t08_oldest_contacted

Run i of every task uses seed i, so run 3 of task A and run 3 of task B see
the same chaos schedule, and a rerun with the same seed replays it exactly.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from agents.browser import ALL_GUARDS, BrowserEnv  # noqa: E402
from tasks.tasks import BY_ID, TASKS, check  # noqa: E402


def http(method, url, data=None):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def start_server(db_path, port):
    env = dict(os.environ, FLAKEBENCH_DB=db_path, PORT=str(port))
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "site", "app.py")], env=env,
                            cwd=os.path.join(ROOT, "site"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            http("POST", base + "/reset", {"seed": 0})
            return proc, base
        except OSError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("site did not start")


def make_agent(args):
    if args.agent == "scripted":
        from agents.scripted import ScriptedAgent
        return ScriptedAgent()
    if args.agent in ("gemini", "ollama"):
        from agents.openai_compat import OpenAICompatAgent
        return OpenAICompatAgent(args.agent, model=args.model, rpm=args.rpm)
    from agents.claude_agent import ClaudeAgent
    return ClaudeAgent(model=args.model or "claude-opus-5-5", effort=args.effort, compact=args.compact)


def run_once(env, agent, task, seed, base, db_path, run_dir, max_steps, chaos_off):
    http("POST", base + "/reset", {"seed": seed, "chaos": "off" if chaos_off else "on"})
    env.new_session(run_dir)
    agent.reset(task, seed)
    steps, last_result, t0 = [], None, time.time()
    for i in range(max_steps):
        g0 = len(env.guard_events)
        obs = env.observe()
        obs_text = env.render(obs)
        t_obs = time.time()
        action, reasoning = agent.step(obs, obs_text, last_result)
        t_act = time.time()
        # what the agent was looking at when it chose (target label from the stale snapshot)
        target = None
        if "id" in action:
            target = next((e for e in obs["elements"] if e["id"] == action["id"]), None)
        modal_at_act = False
        try:
            modal_at_act = env.page.evaluate(
                "() => { const m = document.getElementById('whatsnew'); return !!m && getComputedStyle(m).display !== 'none'; }")
        except Exception:
            pass
        n_tabs_before = len(env.pages)
        result, error = env.act(action)
        shot = env.screenshot(f"step_{i:02d}.png")
        steps.append({
            "i": i, "t_obs": t_obs - t0, "t_act": t_act - t0, "think_s": round(t_act - t_obs, 2),
            "url": obs["url_path"], "dom_hash": obs["dom_hash"], "tabs": len(obs["tabs"]),
            "active_tab": env.active, "tabs_after": len(env.pages), "new_tab": len(env.pages) > n_tabs_before,
            "modal_at_observe": obs["modal_visible"], "modal_at_act": modal_at_act, "loading": obs["loading"],
            "action": action, "target": ({"tag": target["tag"], "label": target["label"]} if target else None),
            "reasoning": (reasoning or "")[:400], "result": result, "error": error,
            "guards": env.guard_events[g0:],
            "screenshot": os.path.relpath(shot, run_dir) if shot else None,
        })
        last_result = error and f"ERROR: {error}" or result
        if action["action"] == "done":
            break
    passed, reason = check(task.id, db_path)
    chaos = http("GET", base + "/api/chaos")
    for c in chaos:
        c["t"] = c.pop("ts") - t0
    return {"task": task.id, "seed": seed, "passed": passed, "reason": reason, "n_steps": len(steps),
            "duration_s": round(time.time() - t0, 1), "agent": agent.name, "steps": steps, "chaos": chaos,
            "hit_step_limit": len(steps) == max_steps and steps[-1]["action"]["action"] != "done"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=["scripted", "claude", "gemini", "ollama"], default="scripted")
    ap.add_argument("--model", default=None, help="model id; defaults: claude-opus-5-5, gemini-3.5-flash-lite, qwen3:8b")
    ap.add_argument("--compact", action="store_true", help="claude: send only task + action log + current page (cheaper)")
    ap.add_argument("--rpm", type=float, default=None, help="max requests per minute (gemini default 14)")
    ap.add_argument("--resume", action="store_true", help="skip runs that already have a trace in --out")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--tasks", default="all")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--max-steps", type=int, default=25)
    ap.add_argument("--port", type=int, default=5055)
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-chaos", action="store_true", help="control run with chaos disabled")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--guards", default="", help="comma list of " + ",".join(ALL_GUARDS) + ", or 'all'")
    args = ap.parse_args()

    tasks = TASKS if args.tasks == "all" else [BY_ID[t] for t in args.tasks.split(",")]
    out = os.path.abspath(args.out or os.path.join(ROOT, "runs", time.strftime("%Y%m%d-%H%M%S") + f"-{args.agent}"))
    os.makedirs(out, exist_ok=True)
    db_path = os.path.join(out, "pipeline.db")
    proc, base = start_server(db_path, args.port)
    agent = make_agent(args)
    guards = ALL_GUARDS if args.guards == "all" else tuple(g for g in args.guards.split(",") if g)
    env = BrowserEnv(base, headless=not args.headed, guards=guards)
    meta = {"agent": agent.name, "guards": list(guards), "runs": args.runs, "max_steps": args.max_steps,
            "chaos": not args.no_chaos, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    if not (args.resume and os.path.exists(os.path.join(out, "meta.json"))):
        json.dump(meta, open(os.path.join(out, "meta.json"), "w"), indent=2)
    try:
        with open(os.path.join(out, "results.jsonl"), "a") as fh:
            for task in tasks:
                for seed in range(args.runs):
                    run_dir = os.path.join(out, task.id, f"run{seed}")
                    if args.resume and os.path.exists(os.path.join(run_dir, "trace.json")):
                        continue
                    try:
                        rec = run_once(env, agent, task, seed, base, db_path, run_dir, args.max_steps, args.no_chaos)
                    except Exception as e:
                        if type(e).__name__ == "RateLimited":
                            print(f"\nStopped: {e}\nFinished runs are saved. Rerun the same command with --resume "
                                  "once the quota resets (usually midnight Pacific).")
                            return
                        raise
                    json.dump(rec, open(os.path.join(run_dir, "trace.json"), "w"), indent=1)
                    fh.write(json.dumps({k: rec[k] for k in ("task", "seed", "passed", "reason", "n_steps", "duration_s")}) + "\n")
                    fh.flush()
                    print(f"{task.id} run{seed}: {'PASS' if rec['passed'] else 'FAIL'} "
                          f"({rec['n_steps']} steps, {rec['duration_s']}s) {'' if rec['passed'] else rec['reason']}", flush=True)
    finally:
        env.close()
        proc.terminate()
    if hasattr(agent, "usage"):
        meta["usage"] = agent.usage
        json.dump(meta, open(os.path.join(out, "meta.json"), "w"), indent=2)
    print(f"\ntraces in {out}\nnext: python analysis/diverge.py {out} && python report/build.py {out}")


if __name__ == "__main__":
    main()
