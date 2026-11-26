"""The agent interface. Implement two methods and flakebench can run your agent.

    class MyAgent:
        name = "my-agent"

        def reset(self, task, seed):
            # new episode: task.prompt is the instruction, seed identifies the run
            ...

        def step(self, obs, obs_text, last_result):
            # obs:         dict with url, url_path, tabs, elements (each with id, tag, label,
            #              href/value/options), text, modal_visible, loading, dom_hash
            # obs_text:    the same page rendered as text (what the built-in LLM agents read)
            # last_result: "ok. ..." or "ERROR: ..." from your previous action (None on step 0)
            # return (action, note) where action is one of:
            #   {"action": "click", "id": 12}
            #   {"action": "type", "id": 5, "text": "hello"}
            #   {"action": "select", "id": 7, "option": "Qualified"}
            #   {"action": "check", "id": 9, "checked": True}
            #   {"action": "goto", "path": "/leads?page=2"}
            #   {"action": "switch_tab", "index": 1}
            #   {"action": "wait", "ms": 1000}
            #   {"action": "done", "summary": "..."}
            # and note is any text you want shown in the trace (e.g. the model's reasoning).
            ...

Run it:  ./flakebench run --agent path/to/my_agent.py:MyAgent

Agents that drive their own browser (Polar, Browser Use, a computer-use loop)
don't need this interface at all: see "Bring your own browser" in the README.
"""
from typing import Optional, Protocol, Tuple


class Agent(Protocol):
    name: str

    def reset(self, task, seed: int) -> None: ...

    def step(self, obs: dict, obs_text: str, last_result: Optional[str]) -> Tuple[dict, str]: ...
