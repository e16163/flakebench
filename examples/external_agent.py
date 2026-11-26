"""Bring your own browser: grade an agent that controls its own browser.

polarbench never touches the browser here. The agent (Polar, Browser Use, a
computer-use loop...) opens the start URL in whatever browser it likes. The
site records what happened and grades the end state.

    ./polarbench serve --port 5055                 # terminal 1
    python examples/external_agent.py              # terminal 2

Replace drive() with a call into your agent. Then: ./polarbench report runs/external-<date>
"""
import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5055"


def post(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def drive(page, prompt):
    """Stand-in for your agent: a naive script that clicks by text, like a weak agent would."""
    page.click("text=Calendar")
    page.click("tr:has-text('Sync with Lumen') >> text=Edit")
    page.fill("#date", "2026-12-04")
    page.click("text=Save event")


if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for seed in range(3):
            ep = post("/api/episode/start", {"task": "t09_move_lumen", "seed": seed, "agent": "example-external"})
            page = browser.new_page()
            page.goto(ep["start_url"])
            try:
                drive(page, ep["prompt"])
            except Exception as e:  # a crashed agent is a failed run, not a crashed benchmark
                print("agent error:", str(e).splitlines()[0])
            page.close()
            print(seed, post("/api/episode/finish"))
        browser.close()
