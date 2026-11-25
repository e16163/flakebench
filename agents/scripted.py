"""A scripted baseline agent (think: a recorded RPA macro).

It knows each task's steps in advance and finds elements by label, but it never
re-plans: no modal handling, no tab switching, no retry. It exists so the whole
pipeline runs without an API key, and as a floor to compare real agents with.
A fake "thinking" delay (seeded) stands in for model latency, which is what
lets the page change between observation and action.
"""
import random
import time

L = "/leads"
PLANS = {
    "t01_qualify_priya": [("goto", L), ("find", "Priya Raman"), ("select", "Stage", "Qualified"), ("click", "Save")],
    "t02_assign_helix": sum([[("goto", L), ("find", n), ("select", "Owner", "Riley"), ("click", "Save")]
                             for n in ("Grace Liu", "Dev Malhotra", "Uma Sorensen")], []),
    "t03_reply_pricing": [("goto", "/inbox"), ("click", "Pricing for 40 seats"),
                          ("type", "Reply", "Thanks Dana. Happy to walk through pricing for 40 seats. Does Thursday work?"),
                          ("click", "Send reply")],
    "t04_intro_quartz": [("goto", "/compose"), ("type", "To", "felix.wagner@quartz.com"),
                         ("type", "Subject", "Intro: Pipeline x Quartz Health"),
                         ("type", "Message", "Hi Felix, quick intro."), ("click", "Send")],
    "t05_schedule_arcadia": [("goto", "/calendar"), ("type", "Title", "Demo with Arcadia"),
                             ("type", "Date", "2026-12-03"), ("type", "Start time", "14:00"),
                             ("select", "Lead", "Rosa Delgado (Arcadia Foods)"), ("click", "Create event")],
    "t06_archive_newsletters": [("goto", "/inbox")] + [("check", f"Select {s}") for s in
                                ("Growth Weekly #212", "SaaS Digest: November", "Webinar reminder", "Growth Weekly #211")]
                               + [("click", "Archive selected")],
    "t07_lose_tomas": [("goto", L), ("find", "Tomás Ortega"), ("select", "Stage", "Lost"),
                       ("type", "Notes", "budget frozen"), ("click", "Save")],
    "t08_oldest_contacted": [("goto", L), ("find", "Hugo Bianchi"), ("select", "Owner", "Riley"), ("click", "Save")],
    "t09_move_lumen": [("goto", "/calendar/1"), ("type", "Date", "2026-12-04"), ("click", "Save event")],
    "t10_note_q1": [("goto", L), ("find", "Leo Strand"), ("type", "Notes", "sent deck"), ("click", "Save")],
}


class ScriptedAgent:
    name = "scripted-baseline"

    def __init__(self, latency=(0.2, 2.2)):
        self.latency = latency

    def reset(self, task, seed):
        self.plan = list(PLANS[task.id])
        self.i = 0
        self.pages_tried = 0
        self.rng = random.Random(f"agent:{seed}:{task.id}")

    def _find(self, obs, label, tag=None):
        for e in obs["elements"]:
            if e["label"] == label and (tag is None or e["tag"] == tag):
                return e["id"]
        return None

    def step(self, obs, obs_text, last_result):
        time.sleep(self.rng.uniform(*self.latency))  # "thinking"
        if self.i >= len(self.plan):
            return {"action": "done", "summary": "script finished"}, "script complete"
        step = self.plan[self.i]
        kind = step[0]
        if kind == "goto":
            self.i += 1
            return {"action": "goto", "path": step[1]}, f"go to {step[1]}"
        if kind == "find":  # click a lead by name, paging forward if needed
            el = self._find(obs, step[1], "a")
            if el:
                self.i += 1
                self.pages_tried = 0
                return {"action": "click", "id": el}, f"open {step[1]}"
            nxt = self._find(obs, "Next", "a")
            if nxt and self.pages_tried < 6:
                self.pages_tried += 1
                return {"action": "click", "id": nxt}, f"{step[1]} not on this page, next page"
            return {"action": "done", "summary": f"could not find {step[1]}"}, "gave up"
        el = self._find(obs, step[1])
        self.i += 1
        if el is None:
            # a macro doesn't re-plan: it skips the step and keeps going
            return {"action": "wait", "ms": 200}, f"element '{step[1]}' not found, skipping"
        if kind == "click":
            return {"action": "click", "id": el}, f"click {step[1]}"
        if kind == "type":
            return {"action": "type", "id": el, "text": step[2]}, f"type into {step[1]}"
        if kind == "select":
            return {"action": "select", "id": el, "option": step[2]}, f"set {step[1]} = {step[2]}"
        if kind == "check":
            return {"action": "check", "id": el, "checked": True}, f"tick {step[1]}"
        raise ValueError(kind)
