"""A minimal custom agent: a few hand-written rules instead of a model.

    ./flakebench run --agent examples/my_agent.py:RuleAgent --tasks t09_move_lumen

Swap the body of step() for a call to your own model or planner.
"""


class RuleAgent:
    name = "rule-agent"

    def reset(self, task, seed):
        self.task = task
        self.did = []

    def step(self, obs, obs_text, last_result):
        if obs["modal_visible"]:  # handle the dialog before anything else
            btn = next((e for e in obs["elements"] if e["label"] == "Dismiss"), None)
            if btn:
                return {"action": "click", "id": btn["id"]}, "close the dialog"
        if "goto" not in self.did:
            self.did.append("goto")
            return {"action": "goto", "path": "/calendar/1"}, "open the event"
        date = next((e for e in obs["elements"] if e["label"] == "Date"), None)
        if date and "date" not in self.did:
            self.did.append("date")
            return {"action": "type", "id": date["id"], "text": "2026-12-04"}, "set the new date"
        save = next((e for e in obs["elements"] if e["label"] == "Save event"), None)
        if save and "save" not in self.did:
            self.did.append("save")
            return {"action": "click", "id": save["id"]}, "save"
        return {"action": "done", "summary": "moved the event"}, "done"
