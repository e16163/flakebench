"""Record a workflow from a real browser run, then replay it on the old and the changed site."""
import json

import pytest

from agents.browser import ALL_GUARDS, BrowserEnv
from agents.scripted import ScriptedAgent
from autopilot.healers import ScriptedHealer
from autopilot.record import record
from autopilot.replay import Autopilot
from autopilot.store import Store
from runner.run import http, run_once, start_server
from tasks.tasks import BY_ID, check


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    d = tmp_path_factory.mktemp("ap")
    db = str(d / "p.db")
    proc, base = start_server(db, 5093)
    env = BrowserEnv(base, guards=ALL_GUARDS)
    yield env, base, db, d
    env.close()
    proc.terminate()
    proc.wait()


def _record(site, task_id):
    env, base, db, d = site
    rec = run_once(env, ScriptedAgent(latency=(0, 0.05)), BY_ID[task_id], 0, base, db, str(d / f"rec-{task_id}"),
                   max_steps=25, chaos_off=True)
    assert rec["passed"], rec["reason"]
    return record(rec, {"goal": BY_ID[task_id].prompt})


def _replay(site, wf, version, healer=None, seed=1, chaos="off"):
    env, base, db, d = site
    http("POST", base + "/reset", {"seed": seed, "chaos": chaos, "version": version})
    env.new_session(str(d / f"rp-{wf['id']}-{version}-{seed}"))
    res = Autopilot(env, healer=healer).run(wf)
    res["passed"], res["check"] = check(wf["id"], db)
    return res


def test_recording_is_clean(site):
    wf = _record(site, "t07_lose_tomas")
    acts = [s["action"] for s in wf["steps"]]
    assert "done" not in acts and "wait" not in acts
    save = wf["steps"][-1]
    assert save["commit"] and save["expect"]["status"] == ["Saved"]
    # the edit form keeps its values after saving, so replay checks them
    assert {v["value"] for v in save["expect"]["values"]} == {"Lost", "{{notes}}"}
    assert wf["params"] == {"notes": "budget frozen"}
    assert any(s.get("nav") for s in wf["steps"])


def test_replay_old_site_uses_no_ai(site):
    wf = _record(site, "t07_lose_tomas")
    res = _replay(site, wf, 1, healer=ScriptedHealer(), chaos="on")
    assert res["passed"] and res["verified"], res
    assert res["ai_calls"] == 0
    assert "new_workflow" not in res


def test_parameters_change_the_input(site):
    wf = _record(site, "t07_lose_tomas")
    env, base, db, d = site
    http("POST", base + "/reset", {"seed": 1, "chaos": "off", "version": 1})
    env.new_session(str(d / "param"))
    res = Autopilot(env).run(wf, params={"notes": "budget frozen until Q3"})
    assert res["verified"]
    import sqlite3
    note = sqlite3.connect(db).execute("SELECT notes FROM leads WHERE name = 'Tomás Ortega'").fetchone()[0]
    assert note == "budget frozen until Q3"


def test_changed_site_without_ai_stops_safely(site):
    wf = _record(site, "t01_qualify_priya")
    res = _replay(site, wf, 2, healer=None)
    assert res["status"] == "failed" and not res["verified"]
    assert "Stage" in res["reason"]
    assert "new_workflow" not in res
    assert not res["passed"]  # nothing was saved on the wrong lead either
    assert "side effect" not in res["check"]


def test_changed_site_heals_once_then_replays_free(site, tmp_path):
    store = Store(str(tmp_path / "wf"))
    store.save(_record(site, "t01_qualify_priya"), "recorded")
    first = _replay(site, store.load("t01_qualify_priya"), 2, healer=ScriptedHealer())
    assert first["passed"] and first["verified"], first
    assert first["ai_calls"] >= 1 and first["counts"]["healed"] == 1
    v = store.save(first["new_workflow"], "; ".join(first["changes"]))
    assert v == 2
    second = _replay(site, store.load("t01_qualify_priya"), 2, healer=ScriptedHealer(), seed=2)
    assert second["passed"] and second["verified"], second
    assert second["ai_calls"] == 0
    assert second["counts"]["auto_fixed"] == 0 and second["counts"]["healed"] == 0
    # the healed radio button is what gets checked after saving now
    save = [s for s in second["steps"] if s["checks"]][-1]
    assert any("Qualified" in name and ok for name, ok in save["checks"])
    # rollback puts version 1 back, and it is still there unchanged
    store.rollback("t01_qualify_priya", 1)
    assert store.load("t01_qualify_priya")["version"] == 1
    assert json.dumps(store.load("t01_qualify_priya", 2)["steps"]) != json.dumps(store.load("t01_qualify_priya", 1)["steps"])


def test_hidden_form_gets_opened_by_healer(site):
    wf = _record(site, "t05_schedule_arcadia")
    res = _replay(site, wf, 2, healer=ScriptedHealer())
    assert res["passed"] and res["verified"], res
    assert any("New event" in a for s in res["steps"] for a in s["healed_actions"])
    # a create form clears itself after saving, so its values aren't expected to stay
    assert not wf["steps"][-1]["expect"].get("values")
