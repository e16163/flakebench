"""One real browser run end to end: site, agent, checker, trace."""
import pytest

from agents.browser import BrowserEnv
from agents.scripted import ScriptedAgent
from runner.run import run_once, start_server
from tasks.tasks import BY_ID


@pytest.mark.parametrize("task_id", ["t09_move_lumen", "t05_schedule_arcadia"])
def test_scripted_passes_without_chaos(tmp_path, task_id):
    db = str(tmp_path / "p.db")
    proc, base = start_server(db, 5092)
    env = BrowserEnv(base)
    try:
        rec = run_once(env, ScriptedAgent(latency=(0, 0.1)), BY_ID[task_id], 0, base, db,
                       str(tmp_path / "run"), max_steps=15, chaos_off=True)
    finally:
        env.close()
        proc.terminate()
        proc.wait()
    assert rec["passed"], rec["reason"]
    assert rec["chaos"] == []
    assert all(s["screenshot"] for s in rec["steps"])
