"""Find where failing runs split from passing runs, and why.

    python analysis/diverge.py runs/<dir>

For each task we align every failing trace against the passing traces of the
same task. Each step is keyed by (action, target label, url, whether it
errored). The divergence point is the first step whose key prefix matches no
passing run. We then label the cause from the trace at that step and check the
label against the site's own chaos log, so "modal" means a modal really was
injected on that page, not that we guessed one.

Writes runs/<dir>/analysis.json.
"""
import glob
import json
import os
import statistics
import sys
from collections import Counter

CAUSES = {
    "modal": "Modal blocked the click",
    "stale_element": "Stale element (list re-rendered while the agent was thinking)",
    "wrong_tab": "Acted on the wrong tab after a link opened a new one",
    "slow_render": "Acted before content loaded",
    "pagination": "Target was on another page and never found",
    "step_limit": "Ran out of steps (looping)",
    "agent_logic": "Agent chose a wrong action (no environmental trigger)",
}
CHAOS_FOR = {"modal": "modal", "stale_element": "reorder", "wrong_tab": "newtab", "slow_render": "slow"}


def key(step):
    a = step["action"]
    label = (step.get("target") or {}).get("label") or a.get("path") or a.get("option") or ""
    return (a["action"], label, step["url"], step["error"] is None)


def divergence_index(fail, passes):
    keys = [key(s) for s in fail["steps"]]
    if not passes:
        errs = [s["i"] for s in fail["steps"] if s["error"]]
        return (errs[0] if errs else len(keys) - 1), False
    pkeys = [[key(s) for s in p["steps"]] for p in passes]
    for k in range(len(keys)):
        if not any(len(pk) > k and pk[:k + 1] == keys[:k + 1] for pk in pkeys):
            return k, True
    return len(keys) - 1, True


def classify(trace, k):
    steps = trace["steps"]
    s = steps[k]
    err = s["error"] or ""
    window = steps[max(0, k - 2):k + 1]
    page = s["url"].split("?")[0].strip("/").split("/")[0] or "leads"
    if "intercepts pointer events" in err or s["modal_at_act"]:
        cause = "modal"
    elif err.startswith("stale element"):
        cause = "stale_element"
    elif any(st["new_tab"] for st in steps[:k + 1]) and s["active_tab"] != s["tabs_after"] - 1:
        cause = "wrong_tab"
    elif s["loading"] or any(st["loading"] and st["action"]["action"] == "wait" for st in window):
        cause = "slow_render"
    elif trace.get("hit_step_limit"):
        cause = "step_limit"
    elif steps[-1]["action"]["action"] == "done" and any(
            w in (steps[-1]["action"].get("summary") or "").lower() for w in ("could not find", "not found", "couldn't find")):
        cause = "pagination"
    else:
        cause = "agent_logic"
    # back the label with the site's ground truth
    kind = CHAOS_FOR.get(cause)
    confirmed = None
    if kind:
        t_act = s["t_act"] + 2.0
        confirmed = any(c["kind"] == kind and c["t"] <= t_act for c in trace["chaos"])
    # if the trace still shows a modal error earlier on, prefer the earliest environmental hit
    return cause, confirmed, page


def analyze(run_dir):
    tasks = {}
    for path in sorted(glob.glob(os.path.join(run_dir, "*", "run*", "trace.json"))):
        t = json.load(open(path))
        t["_dir"] = os.path.relpath(os.path.dirname(path), run_dir)
        tasks.setdefault(t["task"], []).append(t)

    out = {"run_dir": run_dir, "meta": json.load(open(os.path.join(run_dir, "meta.json"))), "tasks": [],
           "causes": {}, "cause_labels": CAUSES}
    total = Counter()
    for task_id, traces in tasks.items():
        traces.sort(key=lambda t: t["seed"])
        passes = [t for t in traces if t["passed"]]
        fails = [t for t in traces if not t["passed"]]
        n = len(traces)
        p = len(passes) / n
        steps = [t["n_steps"] for t in traces]
        durs = [t["duration_s"] for t in traces]
        failures = []
        for f in fails:
            k, had_ref = divergence_index(f, passes)
            cause, confirmed, page = classify(f, k)
            total[cause] += 1
            s = f["steps"][k]
            failures.append({
                "seed": f["seed"], "divergence_step": k, "had_passing_reference": had_ref,
                "cause": cause, "confirmed_by_chaos_log": confirmed, "page": page,
                "action": s["action"], "target": s["target"], "error": s["error"],
                "reasoning": s.get("reasoning", ""), "checker_reason": f["reason"],
                "screenshot": os.path.join(f["_dir"], s["screenshot"]) if s.get("screenshot") else None,
                "n_steps": f["n_steps"],
                "chaos_seen": sorted({c["kind"] for c in f["chaos"]}),
            })
        out["tasks"].append({
            "task": task_id, "runs": n, "passes": len(passes), "pass_rate": round(p, 2),
            "flaky": 0 < len(passes) < n,
            "pass_rate_std": round((p * (1 - p) / n) ** 0.5, 2),
            "steps_mean": round(statistics.mean(steps), 1),
            "steps_std": round(statistics.pstdev(steps), 1),
            "duration_mean_s": round(statistics.mean(durs), 1),
            "duration_std_s": round(statistics.pstdev(durs), 1),
            "results": [{"seed": t["seed"], "passed": t["passed"], "n_steps": t["n_steps"], "reason": t["reason"]} for t in traces],
            "failures": failures,
        })
    out["causes"] = dict(total.most_common())
    allruns = [r for t in out["tasks"] for r in t["results"]]
    out["overall"] = {
        "runs": len(allruns), "passes": sum(r["passed"] for r in allruns),
        "pass_rate": round(sum(r["passed"] for r in allruns) / max(1, len(allruns)), 2),
        "flaky_tasks": sum(t["flaky"] for t in out["tasks"]),
        "always_fail": sum(t["passes"] == 0 for t in out["tasks"]),
        "always_pass": sum(t["passes"] == t["runs"] for t in out["tasks"]),
    }
    json.dump(out, open(os.path.join(run_dir, "analysis.json"), "w"), indent=1)
    return out


if __name__ == "__main__":
    res = analyze(sys.argv[1])
    o = res["overall"]
    print(f"{o['passes']}/{o['runs']} runs passed ({o['pass_rate']:.0%}); "
          f"{o['flaky_tasks']} flaky tasks, {o['always_fail']} always fail, {o['always_pass']} always pass")
    for t in res["tasks"]:
        print(f"  {t['task']:28s} {t['passes']}/{t['runs']}  steps {t['steps_mean']}±{t['steps_std']}")
    print("failure causes:", res["causes"])
