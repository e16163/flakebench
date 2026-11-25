"""A browser agent for any OpenAI-compatible chat API: Gemini (free tier) and
Ollama (local, free) out of the box.

    python runner/run.py --agent gemini                       # needs GEMINI_API_KEY (free at aistudio.google.com)
    python runner/run.py --agent ollama --model qwen3:8b      # needs `ollama serve` running locally

It uses "compact" history: every call sends the task, a one-line log of the
actions so far, and only the current page. Tokens per call stay flat (~2-3k)
instead of growing each step, which is what keeps it inside free-tier limits.
Same tools and prompt as the Claude agent, so results are comparable.
"""
import json
import os
import time
import urllib.error
import urllib.request

from agents.claude_agent import SYSTEM, TOOLS

PRESETS = {
    "gemini": {"base": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "key_env": "GEMINI_API_KEY", "model": "gemini-3.5-flash-lite", "rpm": 14},
    "ollama": {"base": "http://localhost:11434/v1/", "key_env": None, "model": "qwen3:8b", "rpm": 0},
}

OA_TOOLS = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}} for t in TOOLS]


class RateLimited(Exception):
    pass


class OpenAICompatAgent:
    def __init__(self, provider="gemini", model=None, base_url=None, rpm=None, max_retries=8):
        p = PRESETS[provider]
        self.base = (base_url or os.environ.get("OPENAI_COMPAT_BASE_URL") or p["base"]).rstrip("/") + "/"
        self.model = model or p["model"]
        self.key = os.environ.get(p["key_env"], "") if p["key_env"] else "ollama"
        if p["key_env"] and not self.key:
            raise SystemExit(f"Set {p['key_env']} first (free key: https://aistudio.google.com/apikey).")
        self.rpm = p["rpm"] if rpm is None else rpm
        self.max_retries = max_retries
        self.name = f"{provider}:{self.model}"
        self.usage = {"input": 0, "output": 0, "cache_read": 0, "requests": 0, "rate_limit_waits_s": 0.0}
        self._last_call = 0.0

    def reset(self, task, seed):
        self.task = task
        self.log = []          # one line per past action
        self.pending = None    # last action, waiting for its result

    # ---------------------------------------------------------------- http
    def _pace(self):
        if self.rpm:
            gap = 60.0 / self.rpm - (time.time() - self._last_call)
            if gap > 0:
                time.sleep(gap)
        self._last_call = time.time()

    def _post(self, payload):
        req = urllib.request.Request(self.base + "chat/completions", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.key}"})
        delay = 4.0
        for attempt in range(self.max_retries + 1):
            self._pace()
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")[:300]
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    wait = float(e.headers.get("Retry-After") or delay)
                    if e.code == 429 and any(w in body.lower() for w in ("per day", "perday", "per_day")):
                        raise RateLimited(f"daily quota used up: {body}")
                    print(f"  [{self.name}] HTTP {e.code}, retrying in {wait:.0f}s", flush=True)
                    self.usage["rate_limit_waits_s"] += wait
                    time.sleep(wait)
                    delay = min(delay * 2, 120)
                    continue
                raise RuntimeError(f"HTTP {e.code} from {self.base}: {body}") from None
            except urllib.error.URLError as e:
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay = min(delay * 2, 120)
                    continue
                raise RuntimeError(f"cannot reach {self.base}: {e.reason}") from None

    # ---------------------------------------------------------------- step
    def step(self, obs, obs_text, last_result):
        if self.pending is not None:
            self.log.append(f"{len(self.log) + 1}. {json.dumps(self.pending, ensure_ascii=False)} -> {last_result}")
        history = "\n".join(self.log[-30:]) or "(none yet)"
        user = (f"Task: {self.task.prompt}\n\nActions so far:\n{history}\n\nCurrent page:\n{obs_text}\n\n"
                "Choose the next action with exactly one tool call.")
        resp = self._post({"model": self.model, "temperature": 0,
                           "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                           "tools": OA_TOOLS, "tool_choice": "required"})
        self.usage["requests"] += 1
        u = resp.get("usage") or {}
        self.usage["input"] += u.get("prompt_tokens", 0)
        self.usage["output"] += u.get("completion_tokens", 0)
        msg = resp["choices"][0]["message"]
        text = (msg.get("content") or "").strip()
        calls = msg.get("tool_calls") or []
        if not calls:
            self.pending = None
            return {"action": "wait", "ms": 500}, text or "model answered without a tool call"
        fn = calls[0]["function"]
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        action = {"action": fn["name"], **args}
        self.pending = action
        return action, text
