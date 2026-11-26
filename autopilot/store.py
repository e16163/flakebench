"""Saved workflows on disk, with every version kept.

    workflows/<id>/index.json   {"current": 2, "versions": [{"version": 1, ...}, ...]}
    workflows/<id>/v1.json      the workflow as recorded
    workflows/<id>/v2.json      after a run healed a step

A new version is written only after a run that healed something and then
passed verification. Rollback just moves the "current" pointer, so nothing is
ever lost.
"""
import json
import os
import time


class Store:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _dir(self, wf_id):
        return os.path.join(self.root, wf_id)

    def _index(self, wf_id):
        p = os.path.join(self._dir(wf_id), "index.json")
        return json.load(open(p)) if os.path.exists(p) else {"id": wf_id, "current": 0, "versions": []}

    def _write_index(self, wf_id, idx):
        json.dump(idx, open(os.path.join(self._dir(wf_id), "index.json"), "w"), indent=2)

    def ids(self):
        return sorted(d for d in os.listdir(self.root) if os.path.exists(os.path.join(self.root, d, "index.json")))

    def index(self, wf_id):
        return self._index(wf_id)

    def load(self, wf_id, version=None):
        idx = self._index(wf_id)
        v = version or idx["current"]
        return json.load(open(os.path.join(self._dir(wf_id), f"v{v}.json")))

    def save(self, wf, changes):
        """Write wf as the next version and make it current. Returns the version number."""
        os.makedirs(self._dir(wf["id"]), exist_ok=True)
        idx = self._index(wf["id"])
        v = max([x["version"] for x in idx["versions"]] or [0]) + 1
        wf = dict(wf, version=v, created=time.strftime("%Y-%m-%d %H:%M"), changes=changes)
        json.dump(wf, open(os.path.join(self._dir(wf["id"]), f"v{v}.json"), "w"), indent=1)
        idx["versions"].append({"version": v, "created": wf["created"], "changes": changes, "steps": len(wf["steps"])})
        idx["current"] = v
        self._write_index(wf["id"], idx)
        return v

    def rollback(self, wf_id, version):
        idx = self._index(wf_id)
        if version not in [x["version"] for x in idx["versions"]]:
            raise ValueError(f"{wf_id} has no version {version}")
        idx["current"] = version
        idx.setdefault("rollbacks", []).append({"to": version, "at": time.strftime("%Y-%m-%d %H:%M")})
        self._write_index(wf_id, idx)

    def log_run(self, wf_id, summary):
        p = os.path.join(self._dir(wf_id), "runs.jsonl")
        with open(p, "a") as fh:
            fh.write(json.dumps(summary) + "\n")

    def runs(self, wf_id):
        p = os.path.join(self._dir(wf_id), "runs.jsonl")
        return [json.loads(l) for l in open(p)] if os.path.exists(p) else []
