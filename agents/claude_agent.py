"""A Claude browser agent: one tool call per step over a numbered-element view.

Default model is Claude Opus 5.5 at low effort (fast, cheap per step). Pass
--model claude-sonnet-5-5 or claude-haiku-5-5 to compare models: model-vs-model
variance on the same seeds is a nice extra chart for the report.
"""
import json

import anthropic

SYSTEM = """You are a browser agent operating a web CRM called Pipeline at http://localhost.
Each turn you get the current page: URL, open tabs, numbered interactive elements and page text.
Act with exactly one tool call per turn, referring to elements by their [number].
The page can change while you think: dialogs can appear, lists can re-sort, links can open new tabs,
and content can load late. If an action fails, look at the fresh page and adapt.
When the task is complete (or truly impossible), call done with a one-line summary.
Do not change anything the task did not ask for."""

ID = {"type": "integer", "description": "Element number from the latest observation"}


def _tool(name, desc, props):
    return {"name": name, "description": desc, "strict": True,
            "input_schema": {"type": "object", "properties": props,
                             "required": list(props), "additionalProperties": False}}


TOOLS = [
    _tool("click", "Click an element.", {"id": ID}),
    _tool("type", "Replace the contents of an input or textarea.", {"id": ID, "text": {"type": "string"}}),
    _tool("select", "Choose an option in a <select> by its visible text.", {"id": ID, "option": {"type": "string"}}),
    _tool("check", "Tick or untick a checkbox.", {"id": ID, "checked": {"type": "boolean"}}),
    _tool("goto", "Navigate the active tab to a path such as /leads?page=2.", {"path": {"type": "string"}}),
    _tool("switch_tab", "Make another open tab active.", {"index": {"type": "integer"}}),
    _tool("wait", "Wait for the page to finish loading.", {"ms": {"type": "integer"}}),
    _tool("done", "Finish the task.", {"summary": {"type": "string"}}),
]
TOOLS[-1]["cache_control"] = {"type": "ephemeral"}


class ClaudeAgent:
    def __init__(self, model="claude-opus-5-5", effort="low", compact=False):
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        # compact=True: each call sends task + one-line action log + current page only.
        # Flat ~2-3k tokens per call instead of a growing transcript: about 5x cheaper.
        self.compact = compact
        self.name = f"claude:{model}:{effort}" + (":compact" if compact else "")
        self.usage = {"input": 0, "output": 0, "cache_read": 0}

    def reset(self, task, seed):
        self.task = task
        self.messages = []
        self.log = []
        self.pending = None

    def step(self, obs, obs_text, last_result):
        if self.compact:
            if self.pending is not None:
                self.log.append(f"{len(self.log) + 1}. {self.pending} -> {last_result}")
            self.messages = [{"role": "user", "content":
                              f"Task: {self.task.prompt}\n\nActions so far:\n" + ("\n".join(self.log[-30:]) or "(none yet)") +
                              f"\n\nCurrent page:\n{obs_text}"}]
        elif self.pending is None:
            self.messages.append({"role": "user", "content":
                                  f"Task: {self.task.prompt}\n\nCurrent page:\n{obs_text}"})
        else:
            self.messages.append({"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": self.pending,
                "content": f"Result: {last_result}\n\nCurrent page:\n{obs_text}"}]})

        # server-side refusal fallbacks exist for Opus 5.x / Sonnet 5.5 / Fable, not Haiku
        fb = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} \
            if self.model.startswith(("claude-opus-5", "claude-sonnet-5-5", "claude-fable")) else {}
        resp = self.client.beta.messages.create(
            **fb,
            model=self.model,
            max_tokens=4000,
            system=SYSTEM,
            tools=TOOLS,
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},
            messages=self.messages,
        )
        u = resp.usage
        self.usage["input"] += u.input_tokens
        self.usage["output"] += u.output_tokens
        self.usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        # keep the whole content (thinking blocks included) so history stays append-only
        self.messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason == "refusal":
            self.pending = None
            return {"action": "done", "summary": "model refused"}, "refusal"
        text = " ".join(b.text for b in resp.content if b.type == "text").strip()
        call = next((b for b in resp.content if b.type == "tool_use"), None)
        if call is None:
            self.pending = None
            return {"action": "done", "summary": text or "stopped without a tool call"}, text
        action = {"action": call.name, **call.input}
        self.pending = json.dumps(action, ensure_ascii=False) if self.compact else call.id
        return action, text
