"""Run a saved workflow: replay first, fix for free, call AI only when stuck.

For each recorded step:

  1. look      observe the page (waits for it to settle: spinners, aria-busy)
  2. find      the recorded element: exact match, then tolerant match (see match.py)
  3. free fix  if it's still missing: page through a list, skip paging that's no
               longer needed, retry a click that didn't land. Pop-ups, slow
               loads, new tabs and re-sorted lists are handled by the browser
               guards on every action.
  4. act       do it. Steps that send or delete need approval in product.
  5. check     clicks must land on the recorded page. Commits must leave the
               page showing what the recording saw (values that stuck, a status
               line, rows gone). Only then is the step "verified".
  6. heal      if 2-5 fail, hand just this step to a model, with the goal, the
               steps already done and the steps still to come. When the step's
               element shows up (the model opened a form) or the model says the
               step is done, replay takes over again.
  7. fall back if healing runs out of budget, a full agent finishes the task
               from where replay stopped. That run is flagged for review and
               is never saved over the workflow automatically.

A run that healed or re-matched anything and then passed every check saves the
workflow as a new version, so the next run replays the fixed steps for free.
"""
import copy
import json
import re
import time
from dataclasses import dataclass, field

from autopilot.match import _norm, describe, fp_of, next_link, resolve
from autopilot.record import CONFIRM_RE, _path_key

GUARD_TEXT = {
    "settle": "waited for the page to finish loading",
    "overlay": "closed a pop-up that covered the page",
    "follow_tab": "followed the link into the new tab it opened",
    "reresolve": "the list re-sorted under the click; found the same item again",
}


@dataclass
class HealTask:
    """What a healer agent gets. Same shape as tasks.Task (id, prompt) plus context."""
    id: str
    prompt: str
    context: dict = field(default_factory=dict)
    tags: tuple = ()


def _sub(s, params):
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(params.get(m.group(1), m.group(0))), s or "")


def _key(t):
    return json.dumps(t, sort_keys=True)


def _value_desc(step, params):
    if step["action"] == "type":
        return f" with the text \"{_sub(step['text'], params)}\""
    return ""


class Autopilot:
    def __init__(self, env, healer=None, fallback=None, heal_budget=8, fallback_budget=20,
                 approve=None, pace=0.0, log=None):
        self.env = env
        self.healer = healer
        self.fallback = fallback
        self.heal_budget = heal_budget
        self.fallback_budget = fallback_budget
        self.approve = approve or (lambda step, desc: True)
        self.pace = pace
        self.log = log or (lambda *a: None)

    # ------------------------------------------------------------------ helpers
    def _obs(self):
        return self.env.observe()

    def _act(self, action):
        g0 = len(self.env.guard_events)
        if self.pace:
            time.sleep(self.pace)
        res, err = self.env.act(action)
        fixes = [GUARD_TEXT.get(g["guard"], g["detail"]) for g in self.env.guard_events[g0:]]
        return res, err, fixes

    def _action_for(self, step, el, params):
        a = {"action": step["action"], "id": el["id"]}
        if step["action"] == "type":
            a["text"] = _sub(step["text"], params)
        elif step["action"] == "select":
            a["option"] = step["option"]
        elif step["action"] == "check":
            a["checked"] = step.get("checked", True)
        return a

    def _next_target_step(self, i):
        for s in self.steps[i + 1:]:
            if s["action"] == "goto":
                return None
            if not s.get("nav"):
                return s
        return None

    def _page_through(self, step, obs, rec):
        """The element isn't here, but this is a paged list: look on the next pages."""
        if step.get("page", "").split("?")[0] != obs["url_path"].split("?")[0]:
            return None, obs
        hops = []
        for hop in range(1, 11):
            nxt = next_link(obs["elements"])
            if not nxt:
                return None, obs
            hops.append(fp_of(nxt))
            _, err, _ = self._act({"action": "click", "id": nxt["id"]})
            if err:
                return None, obs
            obs = self._obs()
            el, how, why = resolve(step["target"], obs["elements"], step.get("option"))
            if el:
                rec["fixes"].append(f"wasn't on that page any more; found it {hop} page{'s' if hop > 1 else ''} later")
                rec["paged"] = hops
                return el, obs
        return None, obs

    # ------------------------------------------------------------------ healing
    def _heal(self, wf, i, step, problem, params, rec, mode="step"):
        """Ask the healer to fix one step. Returns (outcome, actions).

        outcome: 'unblocked' (the step's element appeared; replay continues with the step),
                 'done' (the healer did the step itself), or 'failed'.
        """
        if not self.healer:
            return "failed", []
        done = [describe(s) + _value_desc(s, params) for s in self.done_steps]
        later = [describe(s) + _value_desc(s, params) for s in self.steps[i + 1:] if not s.get("nav")]
        if mode == "step":
            ask = (f"The step that no longer works on this page: {describe(step)}{_value_desc(step, params)}.\n"
                   f"Why: {problem}\n"
                   "On the current page, either do that one step in whatever way the page now allows, or do the "
                   "smallest thing that makes it possible (for example open the form or menu it is in). Then call done. "
                   "If it can't be done here, call done with a summary starting with 'cannot'.")
        else:
            ask = (f"The step '{describe(step)}' should have left the page showing this, but it doesn't: {problem}\n"
                   "Fix it with as few actions as possible (for example redo the change and save again), then call done. "
                   "If it can't be fixed, call done with a summary starting with 'cannot'.")
        prompt = (f"You are repairing one step of a saved browser workflow. Do not redo the whole task.\n"
                  f"Workflow goal: {wf['goal']}\n"
                  f"Steps already done:\n" + ("\n".join(f"  {n + 1}. {d}" for n, d in enumerate(done)) or "  (none)") +
                  f"\n{ask}\n"
                  "Steps that come after it (do NOT do these, the workflow will):\n" +
                  ("\n".join(f"  - {d}" for d in later) or "  (none)"))
        task = HealTask(id=f"{wf['id']}:heal", prompt=prompt,
                        context={"mode": mode, "step": step, "problem": problem, "params": params, "goal": wf["goal"]})
        self.healer.reset(task, 0)
        actions, last, used = [], None, 0
        while self.ai_calls < self.heal_budget:
            obs = self._obs()
            if mode == "step" and actions and step.get("target"):
                el, _, _ = resolve(step["target"], obs["elements"], step.get("option"))
                if el:
                    return "unblocked", actions
            action, note = self.healer.step(obs, self.env.render(obs), last)
            self.ai_calls += 1
            used += 1
            rec["ai_calls"] += 1
            if action.get("action") == "done":
                summary = _norm(action.get("summary", ""))
                rec["heal_notes"].append(action.get("summary") or note or "done")
                if summary.startswith("cannot"):
                    return "failed", actions
                return "done", actions
            el = next((e for e in obs["elements"] if e["id"] == action.get("id")), None) if "id" in action else None
            if el and CONFIRM_RE.search(el.get("label") or "") and action["action"] == "click":
                if not self.approve(step, f"(AI fix) click '{el['label']}'"):
                    return "failed", actions
            res, err, fixes = self._act(action)
            rec["fixes"].extend(fixes)
            last = f"ERROR: {err}" if err else res
            if err or action["action"] in ("wait", "switch_tab"):
                continue
            new = {"action": action["action"], "healed": True}
            if action["action"] == "goto":
                new["path"] = action["path"]
            else:
                if not el:
                    continue
                new["target"] = fp_of(el)
                new["page"] = _path_key(obs["url_path"])
            if action["action"] == "type":
                txt = str(action.get("text", ""))
                pname = next((k for k, v in params.items() if v == txt), None)
                new["text"] = "{{" + pname + "}}" if pname else txt
            if action["action"] == "select":
                new["option"] = action.get("option")
            if action["action"] == "check":
                new["checked"] = bool(action.get("checked", True))
            rec["healed_actions"].append(describe(new) + _value_desc(new, params))
            actions.append(new)
        rec["heal_notes"].append(f"stopped: AI budget of {self.heal_budget} calls used up")
        return "failed", actions

    def _remap_values(self, step, actions):
        """A field step was replaced (dropdown -> buttons): later checks should look at the new element."""
        if step["action"] not in ("select", "type", "check") or not step.get("target"):
            return
        new_vals = []
        for a in actions:
            t = a.get("target") or {}
            if a["action"] in ("click", "check") and t.get("type") in ("radio", "checkbox"):
                new_vals.append({"target": t, "value": "true"})
            elif a["action"] == "select":
                new_vals.append({"target": t, "value": a["option"]})
            elif a["action"] == "type":
                new_vals.append({"target": t, "value": a["text"]})
        self.remap[_key(step["target"])] = new_vals

    def _expectations(self, step):
        exp = copy.deepcopy(step.get("expect") or {})
        vals = []
        for v in exp.get("values", []):
            k = _key(v["target"])
            if k in self.remap:
                vals.extend(self.remap[k])
            else:
                vals.append(v)
        if vals:
            exp["values"] = vals
        return exp

    # ------------------------------------------------------------------ verify
    def _verify(self, step, params, obs):
        exp = self._expectations(step)
        text = (obs.get("text") or "").lower()
        checks = []
        if step.get("expect_url"):
            got = _path_key(obs["url_path"])
            checks.append((f"landed on {step['expect_url']}", got == step["expect_url"]))
        for tp in exp.get("text_params", []):
            v = _sub(tp, params)
            checks.append((f"page shows \"{v[:40]}\"", v.lower() in text))
        for st in exp.get("status", []):
            words = [w for w in re.findall(r"[a-z]+", st.lower()) if len(w) > 3]
            checks.append((f"page says \"{st}\"", st.lower() in text or any(w in text for w in words)))
        for v in exp.get("values", []):
            el, how, _ = resolve(v["target"], obs["elements"])
            if el is None:
                continue  # that field isn't shown after saving; nothing to compare
            want = _sub(v["value"], params)
            checks.append((f"'{el.get('label')}' is \"{want[:40]}\"", _norm(el.get("value")) == _norm(want)))
        for g in exp.get("gone", []):
            checks.append((f"\"{g}\" is gone", g.lower() not in text))
        return checks

    # ------------------------------------------------------------------ run
    def run(self, wf, params=None):
        params = dict(wf.get("params", {}), **(params or {}))
        self.steps = wf["steps"]
        self.done_steps, self.remap, self.ai_calls, self.verify_healed = [], {}, 0, False
        new_steps, changes, results = [], [], []
        t0 = time.time()
        out = {"workflow": wf["id"], "version": wf["version"], "goal": wf["goal"], "steps": results,
               "status": "passed", "reason": "", "ai_calls": 0, "fallback": None, "saved_version": None}

        i = 0
        while i < len(self.steps):
            step = self.steps[i]
            rec = {"n": i + 1, "desc": describe(step) + _value_desc(step, params), "status": "replayed",
                   "fixes": [], "ai_calls": 0, "checks": [], "verified": None, "heal_notes": [],
                   "healed_actions": [], "t": None}
            ts = time.time()
            results.append(rec)
            keep = copy.deepcopy(step)

            if step["action"] == "goto":
                _, err, fixes = self._act({"action": "goto", "path": step["path"]})
                rec["fixes"] += fixes
                if err:
                    rec["status"], out["status"], out["reason"] = "failed", "failed", err
                    break
                new_steps.append(keep)
                self.done_steps.append(step)
                rec["t"] = round(time.time() - ts, 2)
                i += 1
                continue

            obs = self._obs()
            if step.get("nav"):
                nxt = self._next_target_step(i)
                if nxt and resolve(nxt["target"], obs["elements"], nxt.get("option"))[0]:
                    rec["status"] = "skipped"
                    rec["fixes"].append(f"'{nxt['target'].get('label')}' is already on this page; no paging needed")
                    new_steps.append(keep)
                    rec["t"] = round(time.time() - ts, 2)
                    i += 1
                    continue

            el, how, why = resolve(step["target"], obs["elements"], step.get("option"))
            if el is None and not step.get("nav"):
                el, obs = self._page_through(step, obs, rec)
                how = "exact" if el else None
                if el:
                    # learn it: the next version pages that far by itself
                    for t in rec.pop("paged"):
                        new_steps.append({"action": "click", "target": t, "page": step["page"], "nav": True})
                    changes.append(f"step {i + 1}: '{step['target'].get('label')}' moved to a later page")
            if el is None and step.get("nav"):
                # the pager changed or we're already past it: the next step pages by itself
                rec["status"] = "skipped"
                rec["fixes"].append("paging control not found; the next step will look through the list itself")
                new_steps.append(keep)
                rec["t"] = round(time.time() - ts, 2)
                i += 1
                continue
            if how == "tolerant":
                rec["fixes"].append(f"matched '{el.get('label')}' instead of '{step['target'].get('label')}' "
                                    f"({', '.join(why)})")
                keep["target"] = fp_of(el)
                changes.append(f"step {i + 1}: '{step['target'].get('label')}' is now '{el.get('label')}'")
                if step["action"] in ("select", "type", "check"):
                    self.remap[_key(step["target"])] = [{"target": keep["target"], "value": (
                        step["option"] if step["action"] == "select" else
                        step.get("text") if step["action"] == "type" else str(step.get("checked", True)).lower())}]

            healed_now = False
            if el is None:
                outcome, actions = self._heal(wf, i, step, "; ".join(why) or "element not found", params, rec)
                if outcome == "unblocked":
                    rec["status"] = "healed"
                    new_steps.extend(actions)
                    changes.append(f"step {i + 1}: AI added {len(actions)} step(s) before '{describe(step)}'")
                    obs = self._obs()
                    el, how, why = resolve(step["target"], obs["elements"], step.get("option"))
                    healed_now = True
                elif outcome == "done":
                    rec["status"] = "healed"
                    if not actions:
                        actions = [keep]  # the AI found nothing to do; keep the step as it was
                    if actions and step.get("commit"):
                        last_click = next((a for a in reversed(actions) if a["action"] == "click"), None)
                        if last_click:
                            for k in ("commit", "confirm", "expect", "expect_url"):
                                if k in step:
                                    last_click[k] = step[k]
                    self._remap_values(step, actions)
                    for a in actions:
                        if a.get("expect"):
                            a["expect"] = self._expectations(a)
                    new_steps.extend(actions)
                    changes.append(f"step {i + 1}: AI replaced '{describe(step)}' with "
                                   + "; ".join(describe(a) for a in actions))
                    self.done_steps.append(step)
                    if step.get("commit"):
                        obs = self._obs()
                        if not self._check_commit(wf, i, step, params, obs, rec, out):
                            break
                    rec["t"] = round(time.time() - ts, 2)
                    i += 1
                    continue
                if el is None:
                    if self._fallback(wf, i, params, rec, out):
                        rec["t"] = round(time.time() - ts, 2)
                        break
                    rec["status"], out["status"] = "failed", "failed"
                    out["reason"] = f"step {i + 1} ({describe(step)}) could not be done: " + (
                        "; ".join(why) if not self.healer else "healing failed")
                    rec["t"] = round(time.time() - ts, 2)
                    break

            if step.get("confirm") and not self.approve(step, rec["desc"]):
                rec["status"], out["status"], out["reason"] = "waiting", "waiting", f"step {i + 1} needs approval"
                break
            if step.get("confirm"):
                rec["approval"] = "approved"

            res, err, fixes = self._act(self._action_for(step, el, params))
            rec["fixes"] += fixes
            if err:
                # one free retry on a fresh look at the page
                obs = self._obs()
                el2, how2, _ = resolve(step["target"], obs["elements"], step.get("option"))
                if el2:
                    res, err, fixes = self._act(self._action_for(step, el2, params))
                    rec["fixes"] += fixes + (["retried after: " + err.split(":")[0]] if not err else [])
            if err:
                rec["status"], out["status"], out["reason"] = "failed", "failed", f"step {i + 1}: {err}"
                break

            if rec["fixes"] and rec["status"] == "replayed":
                rec["status"] = "auto_fixed"
            if step.get("expect"):
                keep["expect"] = self._expectations(step)
            new_steps.append(keep)
            self.done_steps.append(step)

            if step.get("commit") or step.get("expect_url"):
                obs = self._obs()
                if step.get("commit"):
                    if not self._check_commit(wf, i, step, params, obs, rec, out):
                        break
                elif _path_key(obs["url_path"]) != step["expect_url"]:
                    # a click that didn't land: retry once, for free
                    el3, _, _ = resolve(step["target"], obs["elements"])
                    if el3:
                        self._act(self._action_for(step, el3, params))
                        obs = self._obs()
                    ok = _path_key(obs["url_path"]) == step["expect_url"]
                    rec["checks"] = [(f"landed on {step['expect_url']}", ok)]
                    if ok:
                        rec["fixes"].append("the click didn't land the first time; clicked again")
                        rec["status"] = "auto_fixed" if rec["status"] == "replayed" else rec["status"]
                    else:
                        rec["status"], out["status"] = "failed", "failed"
                        out["reason"] = f"step {i + 1}: expected to reach {step['expect_url']}, got {obs['url_path']}"
                        break
            if healed_now:
                rec["status"] = "healed"
            rec["t"] = round(time.time() - ts, 2)
            i += 1

        out["ai_calls"] = self.ai_calls
        out["duration_s"] = round(time.time() - t0, 1)
        out["counts"] = {k: sum(1 for r in results if r["status"] == k)
                         for k in ("replayed", "auto_fixed", "healed", "skipped", "failed")}
        commits = [r for r in results if r["verified"] is not None]
        out["verified"] = out["status"] == "passed" and out["fallback"] is None and all(r["verified"] for r in commits)
        if self.verify_healed:
            out["review"] = "a check only passed after an AI fix; the workflow was not updated automatically"
        if out["status"] == "passed" and out["fallback"] is None and changes and out["verified"] and not self.verify_healed:
            out["new_workflow"] = dict(wf, steps=new_steps)
            out["changes"] = changes
        return out

    def _check_commit(self, wf, i, step, params, obs, rec, out):
        checks = self._verify(step, params, obs)
        rec["checks"] = checks
        if all(ok for _, ok in checks):
            rec["verified"] = True
            return True
        failed = "; ".join(c for c, ok in checks if not ok)
        outcome, actions = self._heal(wf, i, step, failed, params, rec, mode="verify")
        if outcome != "failed":
            self.verify_healed = True
            obs = self._obs()
            checks = self._verify(step, params, obs)
            rec["checks"] = checks
            if all(ok for _, ok in checks):
                rec["verified"] = True
                rec["status"] = "healed"
                return True
        rec["verified"] = False
        rec["status"] = "failed"
        out["status"] = "failed"
        out["reason"] = (f"step {i + 1} ({describe(step)}) didn't check out: {failed}. "
                         "Stopped instead of guessing; flagged for review.")
        return False

    def _fallback(self, wf, i, params, rec, out):
        """Healing didn't work: let a full agent finish the task from here. Flagged, never auto-saved."""
        if not self.fallback:
            return False
        done = [describe(s) + _value_desc(s, params) for s in self.done_steps]
        prompt = (f"{wf['goal']}\n\nA saved workflow already did these steps (don't repeat them):\n" +
                  ("\n".join(f"  {n + 1}. {d}" for n, d in enumerate(done)) or "  (none)") +
                  "\nFinish the task from the current page.")
        task = HealTask(id=f"{wf['id']}:fallback", prompt=prompt, context={"mode": "task", "goal": wf["goal"]})
        self.fallback.reset(task, 0)
        last, calls = None, 0
        while calls < self.fallback_budget:
            obs = self._obs()
            action, note = self.fallback.step(obs, self.env.render(obs), last)
            calls += 1
            self.ai_calls += 1
            rec["ai_calls"] += 1
            if action.get("action") == "done":
                break
            res, err, fixes = self._act(action)
            last = f"ERROR: {err}" if err else res
        rec["status"] = "healed"
        rec["heal_notes"].append(f"healing failed; a full agent finished the task in {calls} AI calls")
        out["fallback"] = {"ai_calls": calls}
        out["reason"] = "finished by the full agent; workflow flagged for re-recording"
        return True
