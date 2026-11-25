"""The site resets to the same data and replays the same chaos for the same seed."""
import json
import os
import urllib.request

import pytest

from runner.run import http, start_server


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    proc, base = start_server(str(tmp_path_factory.mktemp("s") / "p.db"), 5091)
    yield base
    proc.terminate()


def chaos_for(base, seed, loads=12):
    http("POST", base + "/reset", {"seed": seed})
    for _ in range(loads):
        urllib.request.urlopen(base + "/leads").read()
    return [(c["page"], c["kind"], json.dumps(c["detail"])) for c in http("GET", base + "/api/chaos")]


def test_same_seed_same_chaos(site):
    assert chaos_for(site, 7) == chaos_for(site, 7)


def test_different_seed_different_chaos(site):
    assert chaos_for(site, 7) != chaos_for(site, 8)


def test_chaos_off(site):
    http("POST", site + "/reset", {"seed": 1, "chaos": "off"})
    for _ in range(5):
        urllib.request.urlopen(site + "/leads").read()
    assert http("GET", site + "/api/chaos") == []


def test_reset_restores_data(site):
    http("POST", site + "/reset", {"seed": 0})
    data = urllib.parse.urlencode({"stage": "Won", "owner": "Riley", "notes": "x"}).encode()
    urllib.request.urlopen(urllib.request.Request(site + "/leads/7", data=data, method="POST")).read()
    assert b"Won" in urllib.request.urlopen(site + "/leads/7").read()
    http("POST", site + "/reset", {"seed": 0, "chaos": "off"})
    page = urllib.request.urlopen(site + "/leads/7").read().decode()
    assert "<option selected>Contacted</option>" in page
