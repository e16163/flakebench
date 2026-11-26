"""Who autopilot asks when a step no longer fits the page.

Any agent with the normal interface works (reset(task, seed), step(obs,
obs_text, last_result)). The heal prompt is in task.prompt, so the built-in
Gemini, Ollama and Claude agents need no changes.

The escalation ladder is two agents: a small, cheap model for one-step repairs
(--heal-model) and, only if that fails, a full agent to finish the task
(--fallback-model). Both default to the same provider.

ScriptedHealer is a stand-in so the tests and the no-key demo can exercise the
healing path. It knows two generic tricks (a dropdown that became a row of
buttons, a form hidden behind a "New ..." button) and nothing else. Numbers
from it are not evidence of anything; real numbers come from a real model.
"""
import re

from autopilot.match import _norm

OPENERS = re.compile(r"^(new|add|create|open|show|edit)\b", re.I)


class ScriptedHealer:
    name = "scripted-healer (stand-in)"

    def reset(self, task, seed):
        self.ctx = task.context
        self.tried = set()

    def step(self, obs, obs_text, last_result):
        if self.ctx.get("mode") != "step":
            return {"action": "done", "summary": "cannot: stand-in only repairs single steps"}, ""
        step = self.ctx["step"]
        els = obs["elements"]
        if "clicked" in self.tried:
            return {"action": "done", "summary": "did the step"}, ""
        # a dropdown choice that became radio buttons / a button row
        if step["action"] == "select":
            want = _norm(step.get("option"))
            for e in els:
                if _norm(e.get("label")) == want and (e.get("type") in ("radio", "checkbox") or e["tag"] == "button"):
                    self.tried.add("clicked")
                    return {"action": "click", "id": e["id"]}, f"'{step['option']}' is now a button, not a dropdown"
        # the field is inside something that has to be opened first
        if "opened" not in self.tried:
            for e in els:
                if e["tag"] in ("button", "a") and OPENERS.match(e.get("label") or "") and e.get("type") == "button":
                    self.tried.add("opened")
                    return {"action": "click", "id": e["id"]}, f"open '{e['label']}' to reveal the form"
        return {"action": "done", "summary": "cannot: no repair found"}, ""


def make_healer(kind, model=None, rpm=None):
    """kind: none | scripted | gemini | ollama | claude | path.py:Class"""
    if kind in (None, "", "none"):
        return None
    if kind == "scripted":
        return ScriptedHealer()
    if ":" in kind:
        from runner.run import load_agent_class
        return load_agent_class(kind)()
    if kind in ("gemini", "ollama"):
        from agents.openai_compat import OpenAICompatAgent
        return OpenAICompatAgent(kind, model=model, rpm=rpm)
    if kind == "claude":
        from agents.claude_agent import ClaudeAgent
        return ClaudeAgent(model=model or "claude-haiku-5-5", effort="low", compact=True)
    raise SystemExit(f"unknown healer {kind!r}")
