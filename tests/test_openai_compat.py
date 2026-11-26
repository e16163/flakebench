"""Gemini/Ollama agent against a local fake OpenAI-compatible server, including a 429 retry."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agents.openai_compat import OpenAICompatAgent, RateLimited
from tasks.tasks import BY_ID

SEEN, MODE = [], {"first_429": True, "daily": False}


class Fake(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if MODE["daily"]:
            return self._send(429, {"error": {"message": "Quota exceeded for metric generate_requests_per_day"}})
        if MODE["first_429"]:
            MODE["first_429"] = False
            return self._send(429, {"error": {"message": "slow down"}}, retry_after="0")
        SEEN.append(body)
        call = {"id": "c1", "type": "function", "function": {"name": "click", "arguments": json.dumps({"id": 14})}}
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [call]}}],
                         "usage": {"prompt_tokens": 900, "completion_tokens": 12}})

    def _send(self, code, obj, retry_after=None):
        out = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if retry_after is not None:
            self.send_header("Retry-After", retry_after)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def server(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1/")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    yield
    srv.shutdown()


def test_retry_then_tool_call_and_compact_history(server):
    agent = OpenAICompatAgent("gemini", rpm=0)
    agent.reset(BY_ID["t01_qualify_priya"], 0)
    a1, _ = agent.step(None, "URL: /leads", None)
    a2, _ = agent.step(None, "URL: /leads?page=4", "ok.")
    assert a1 == a2 == {"action": "click", "id": 14}
    # second call carries the action log and only the newest page
    user = SEEN[-1]["messages"][1]["content"]
    assert '1. {"action": "click", "id": 14} -> ok.' in user and "URL: /leads?page=4" in user
    assert "URL: /leads\n" not in user
    assert SEEN[-1]["tools"][0]["function"]["name"] == "click"
    assert agent.usage["requests"] == 2 and agent.usage["input"] == 1800


def test_daily_quota_stops_cleanly(server):
    MODE["daily"] = True
    agent = OpenAICompatAgent("gemini", rpm=0)
    agent.reset(BY_ID["t01_qualify_priya"], 0)
    with pytest.raises(RateLimited):
        agent.step(None, "URL: /leads", None)
    MODE["daily"] = False


def test_daily_quota_found_anywhere_in_long_body():
    from agents.openai_compat import is_daily_quota
    body = '[{"error": {"code": 429, "message": "' + "x" * 600 + '", "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}]'
    assert is_daily_quota(body)
    assert not is_daily_quota('{"error": {"code": 429, "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}}')


def test_persistent_429_stops_cleanly(monkeypatch):
    import io
    import urllib.error
    import pytest
    from agents import openai_compat as oc
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    a = oc.OpenAICompatAgent("gemini", rpm=0, max_retries=2)

    def boom(*args, **kw):
        raise urllib.error.HTTPError("u", 429, "Too Many Requests", {"Retry-After": "0"}, io.BytesIO(b'{"error":"slow down"}'))
    monkeypatch.setattr(oc.urllib.request, "urlopen", boom)
    monkeypatch.setattr(oc.time, "sleep", lambda s: None)
    with pytest.raises(oc.RateLimited):
        a._post({})
