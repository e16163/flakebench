"""The Claude agent sends a valid request and parses tool calls, against a local fake API."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from agents.claude_agent import ClaudeAgent
from tasks.tasks import BY_ID

SEEN = []


class Fake(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append({"path": self.path, "body": body, "beta": self.headers.get("anthropic-beta")})
        n = len(SEEN)
        content = [{"type": "text", "text": "Priya is on page 4."},
                   {"type": "tool_use", "id": f"tu_{n}", "name": "click", "input": {"id": 14}}] if n == 1 else \
                  [{"type": "tool_use", "id": f"tu_{n}", "name": "done", "input": {"summary": "moved"}}]
        out = json.dumps({"id": f"msg_{n}", "type": "message", "role": "assistant", "model": body["model"],
                          "content": content, "stop_reason": "tool_use", "stop_sequence": None,
                          "usage": {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 50}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def test_request_shape_and_loop(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    agent = ClaudeAgent()
    agent.reset(BY_ID["t01_qualify_priya"], 0)
    a1, said = agent.step(None, "URL: /leads", None)
    assert a1 == {"action": "click", "id": 14} and "page 4" in said
    a2, _ = agent.step(None, "URL: /leads?page=4", "ok.")
    assert a2["action"] == "done"
    srv.shutdown()

    first, second = SEEN[0]["body"], SEEN[1]["body"]
    assert first["model"] == "claude-opus-5-5"
    assert first["output_config"] == {"effort": "low"}
    assert first["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert all(t["strict"] and t["input_schema"]["additionalProperties"] is False for t in first["tools"])
    assert "server-side-fallback-2026-07-01" in SEEN[0]["beta"]
    # history is append-only: turn 2 starts with turn 1's exact messages
    assert second["messages"][:1] == first["messages"]
    assert second["messages"][-1]["content"][0]["tool_use_id"] == "tu_1"
    assert agent.usage == {"input": 200, "output": 20, "cache_read": 100}


def test_compact_mode_sends_one_message(monkeypatch):
    SEEN.clear()
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    agent = ClaudeAgent(model="claude-haiku-5-5", compact=True)
    agent.reset(BY_ID["t01_qualify_priya"], 0)
    agent.step(None, "URL: /leads", None)
    agent.step(None, "URL: /leads?page=4", "ok.")
    srv.shutdown()
    second = SEEN[1]["body"]
    assert second["model"] == "claude-haiku-5-5"
    assert len(second["messages"]) == 1
    assert "fallbacks" not in second and not SEEN[1]["beta"]
    assert '1. {"action": "click", "id": 14} -> ok.' in second["messages"][0]["content"]
