"""Bring-your-own-browser mode: the site records an outside agent and grades it."""
import json
import os
import urllib.request

from playwright.sync_api import sync_playwright

from agents.browser import chromium_path
from runner.run import start_server


def post(base, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def test_external_agent_is_recorded_and_graded(tmp_path):
    proc, base = start_server(str(tmp_path / "p.db"), 5093)
    try:
        ep = post(base, "/api/episode/start", {"task": "t09_move_lumen", "seed": 0, "chaos": False,
                                               "agent": "test", "out": str(tmp_path / "out")})
        assert "Sync with Lumen" in ep["prompt"]
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=chromium_path())
            page = b.new_page()
            page.goto(ep["start_url"])
            page.click("text=Calendar")
            page.click("tr:has-text('Sync with Lumen') >> text=Edit")
            page.fill("#date", "2026-12-04")
            page.dispatch_event("#date", "change")
            page.click("text=Save event")
            page.wait_for_timeout(300)
            b.close()
        res = post(base, "/api/episode/finish")
        assert res["passed"], res
        trace = json.load(open(os.path.join(res["trace"], "trace.json")))
        labels = [s["target"]["label"] for s in trace["steps"] if s["target"]]
        assert labels[:2] == ["Calendar", "Edit"] and "Save event" in labels
        assert trace["source"] == "recorder"
    finally:
        proc.terminate()
        proc.wait()
