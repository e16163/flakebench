"""Turn one passing agent run into a saved workflow.

The agent already did the hard part: it worked out which clicks and fields get
the job done. Recording keeps that path and throws away the rest:

  * steps that errored, waits, tab switches (replay follows new tabs itself)
    and the final "done"
  * repeated typing into the same field (only the last value counts)

and adds what replay needs to run it safely later without a model:

  * a fingerprint of each element (label, link, field name, role, group...)
  * which clicks are just paging through a list ("nav"), so replay can skip or
    extend them when records move
  * which clicks commit a change ("commit"), and what the page showed right
    after: values that stuck, a status line like "Saved", rows that disappeared.
    Replay checks the same things before it calls a step verified.
  * which commits need a person's OK in product (send, archive, delete...)
  * typed text as named parameters, so "follow up" can become any note
"""
import json
import os
import re
import time

from autopilot.match import NEXT_RE, _href, _norm

STATUS_RE = re.compile(r"^(?:[\w ]{0,20}\b)?(saved|sent|created|updated|archived|deleted|done|success\w*)\b[.!]?$", re.I)
CONFIRM_RE = re.compile(r"\b(send|reply|archive|delete|remove|pay|purchase|buy|submit order|transfer)\b", re.I)


def _is_nav(step):
    a, t = step["action"], step.get("target") or {}
    if a == "goto":
        return "page=" in step["action_raw"].get("path", "")
    if a != "click" or t.get("tag") not in ("a", "button"):
        return False
    label = _norm(t.get("label"))
    return bool(NEXT_RE.match(label) or label in ("prev", "previous", "previous page", "‹", "«")
                or (label.isdigit() and "page=" in _href(t.get("href"))))


def _is_commit(step):
    t = step.get("target") or {}
    return step["action"] == "click" and (t.get("type") == "submit" or t.get("tag") == "button" and t.get("type") != "button")


def _param_name(label, used, value):
    base = re.sub(r"[^a-z0-9]+", "_", _norm(label)).strip("_") or "text"
    name, n = base, 2
    while name in used and used[name] != value:
        name, n = f"{base}_{n}", n + 1
    return name


def _path_key(url):
    """Path plus query keys (not values, not page): /leads/7?saved=1 -> /leads/7?saved"""
    path, _, q = (url or "").partition("?")
    keys = sorted(k.split("=")[0] for k in q.split("&") if k and not k.startswith("page="))
    return path + ("?" + "&".join(keys) if keys else "")


def _held(p, fields_after, params):
    """Did the value set by pending step p show on the page right after the commit, in the recording?
    Only then do we expect it on replay (a 'create' form clears itself; an 'edit' form keeps values)."""
    from autopilot.match import resolve
    els = [dict(f["target"] or {}, id=n, value=f.get("value"), extra={}) for n, f in enumerate(fields_after)]
    for e in els:
        for k in ("name", "dom_id", "role", "group"):
            if k in e:
                e["extra"][k] = e.pop(k)
    el, how, _ = resolve(p["target"], els)
    if not el or how != "exact":
        return False
    want = p["option"] if p["action"] == "select" else (
        str(p["checked"]).lower() if p["action"] == "check" else p.get("text"))
    return _norm(el.get("value")) == _norm(want)


def record(trace, source=None):
    """trace: a runner trace dict (trace.json) of a passing run. Returns a workflow dict."""
    raw = trace["steps"]
    steps = []
    for i, s in enumerate(raw):
        a = s["action"]["action"]
        if a in ("done", "wait", "switch_tab") or s.get("error"):
            continue
        if a != "goto" and not s.get("target"):
            continue
        # what the page looked like right after this action = the next observation
        after = raw[i + 1] if i + 1 < len(raw) else None
        st = {"action": a, "action_raw": s["action"], "page": s["url"], "target": s.get("target"),
              "lines_before": s.get("lines") or [], "after_url": after and after["url"],
              "lines_after": (after and after.get("lines")) or [],
              "fields_after": (after and after.get("fields")) or []}
        if a == "goto":
            st["path"] = s["action"]["path"]
        if a == "type":
            st["text"] = s["action"].get("text", "")
        if a == "select":
            st["option"] = s["action"].get("option")
        if a == "check":
            st["checked"] = bool(s["action"].get("checked", True))
        # typing twice into the same field: keep the last value
        if a == "type" and steps and steps[-1]["action"] == "type" and steps[-1]["target"] == st["target"]:
            steps[-1] = st
            continue
        # two gotos in a row: the first one did nothing useful
        if a == "goto" and steps and steps[-1]["action"] == "goto":
            steps[-1] = st
            continue
        steps.append(st)

    params, out, pending = {}, [], []   # pending: field changes since the last commit
    for st in steps:
        step = {"action": st["action"]}
        for k in ("path", "option", "checked"):
            if k in st:
                step[k] = st[k]
        if st["action"] != "goto":
            step["target"] = st["target"]
            step["page"] = _path_key(st["page"])
        if st["action"] == "type":
            name = _param_name(st["target"].get("label") or st["target"].get("name"), params, st["text"])
            params[name] = st["text"]
            step["text"] = "{{" + name + "}}"
        if _is_nav(st):
            step["nav"] = True
        if st["action"] in ("type", "select", "check"):
            pending.append(st)
        if st["action"] == "click" and not step.get("nav") and st["after_url"] is not None:
            step["expect_url"] = _path_key(st["after_url"])
        if _is_commit(st):
            step["commit"] = True
            if CONFIRM_RE.search(st["target"].get("label") or ""):
                step["confirm"] = True
            before, after_l = set(st["lines_before"]), st["lines_after"]
            after_text = "\n".join(after_l).lower()
            exp = {}
            typed = [p["text"] for p in pending if p["action"] == "type" and len(p["text"].strip()) >= 4]
            shown = [t for t in typed if t.lower() in after_text and not any(t.lower() in b.lower() for b in before)]
            if shown:
                exp["text_params"] = [
                    "{{" + next(k for k, v in params.items() if v == t) + "}}" for t in shown]
            status = [l for l in after_l if l not in before and STATUS_RE.match(l)]
            if status:
                exp["status"] = status[:2]
            stuck = []
            for p in pending:
                if _held(p, st["fields_after"], params):
                    if p["action"] == "select":
                        stuck.append({"target": p["target"], "value": p["option"]})
                    elif p["action"] == "check":
                        stuck.append({"target": p["target"], "value": str(p["checked"]).lower()})
                    elif p["action"] == "type":
                        stuck.append({"target": p["target"], "value": "{{" + next(
                            k for k, v in params.items() if v == p["text"]) + "}}"})
            if stuck:
                exp["values"] = stuck
            gone = []
            for p in pending:
                if p["action"] == "check" and p.get("checked", True):
                    lab = re.sub(r"^select\s+", "", p["target"].get("label") or "", flags=re.I)
                    if lab and any(lab in b for b in before) and lab.lower() not in after_text:
                        gone.append(lab)
            if gone:
                exp["gone"] = gone
            step["expect"] = exp
            pending = []
        out.append(step)

    return {
        "id": trace["task"],
        "goal": source.get("goal") if source else trace["task"],
        "version": 1,
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "source": {"agent": trace.get("agent"), "seed": trace.get("seed"), "run": (source or {}).get("run"),
                   "site_version": trace.get("site_version", 1), "agent_steps": trace.get("n_steps")},
        "params": params,
        "steps": out,
        "changes": "Recorded from a passing agent run.",
    }


def record_run_dir(run_dir, tasks=None):
    """Pick the first passing trace per task in a runner output dir. Returns {task_id: workflow}."""
    from tasks.tasks import BY_ID
    found = {}
    for task_id in sorted(os.listdir(run_dir)):
        tdir = os.path.join(run_dir, task_id)
        if not os.path.isdir(tdir) or (tasks and task_id not in tasks):
            continue
        runs = sorted((d for d in os.listdir(tdir) if d.startswith("run")), key=lambda d: int(d[3:]))
        for r in runs:
            p = os.path.join(tdir, r, "trace.json")
            if not os.path.exists(p):
                continue
            tr = json.load(open(p))
            if tr.get("passed"):
                goal = BY_ID[task_id].prompt if task_id in BY_ID else task_id
                found[task_id] = record(tr, {"goal": goal, "run": os.path.relpath(p)})
                break
    return found
