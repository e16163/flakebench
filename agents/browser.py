"""Browser environment shared by every agent.

observe() numbers the visible interactive elements (like Browser Use does) and
returns a text view of the page. act() executes one action against those
numbers. The gap between the two (the agent's thinking time) is where most
real-world flakiness lives: the page keeps changing while the agent thinks.
"""
import hashlib
import os
import re
import time

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

OBSERVE_JS = r"""
() => {
  document.querySelectorAll('[data-fb-id]').forEach(e => e.removeAttribute('data-fb-id'));
  const sel = 'a, button, input, select, textarea, [role=button]';
  const visible = e => {
    const r = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const labelOf = e => {
    if (e.getAttribute('aria-label')) return e.getAttribute('aria-label');
    if (e.id) { const l = document.querySelector(`label[for="${e.id}"]`); if (l) return l.innerText.trim(); }
    if (e.placeholder) return e.placeholder;
    return (e.innerText || e.value || e.name || '').trim().replace(/\s+/g, ' ').slice(0, 80);
  };
  const out = [];
  let n = 0;
  for (const e of document.querySelectorAll(sel)) {
    if (!visible(e)) continue;
    const id = ++n;
    e.setAttribute('data-fb-id', id);
    const tag = e.tagName.toLowerCase();
    const item = {id, tag, label: labelOf(e)};
    if (tag === 'a') item.href = e.getAttribute('href') + (e.target === '_blank' ? ' (opens new tab)' : '');
    if (tag === 'input') { item.type = e.type; item.value = e.type === 'checkbox' ? String(e.checked) : e.value; }
    if (tag === 'textarea') item.value = e.value;
    if (tag === 'select') { item.value = e.options[e.selectedIndex]?.text; item.options = [...e.options].map(o => o.text); }
    out.push(item);
  }
  const modal = document.getElementById('whatsnew');
  return {
    elements: out,
    text: document.body.innerText.replace(/\n{3,}/g, '\n\n').slice(0, 4000),
    title: document.title,
    modal_visible: !!modal && getComputedStyle(modal).display !== 'none',
    loading: document.body.innerText.includes('Loading messages'),
  };
}
"""


def chromium_path():
    p = os.environ.get("CHROMIUM_PATH")
    if p:
        return p
    return "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None


def element_line(e):
    s = f"[{e['id']}] {e['tag']}"
    if e.get("type"):
        s += f" type={e['type']}"
    s += f' "{e["label"]}"'
    if e.get("href"):
        s += f" -> {e['href']}"
    if "value" in e and e["value"] not in (None, ""):
        s += f' value="{e["value"]}"'
    if e.get("options"):
        s += " options=" + "|".join(e["options"])
    return s


def dom_hash(obs):
    """Structural fingerprint of the page: element kinds and labels, no ids."""
    sig = obs["url_path"] + "\n" + "\n".join(
        f"{e['tag']}:{e['label']}:{e.get('value', '')}" for e in obs["elements"])
    return hashlib.sha1(sig.encode()).hexdigest()[:10]


class BrowserEnv:
    def __init__(self, base_url, headless=True, screenshot_dir=None):
        self.base = base_url.rstrip("/")
        self.headless = headless
        self.shot_dir = screenshot_dir
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=headless, executable_path=chromium_path())
        self.ctx = None
        self.active = 0
        self.last_obs = None

    # -------------------------------------------------------------- lifecycle
    def new_session(self, shot_dir):
        if self.ctx:
            self.ctx.close()
        self.shot_dir = shot_dir
        os.makedirs(shot_dir, exist_ok=True)
        self.ctx = self.browser.new_context(viewport={"width": 1280, "height": 860})
        self.ctx.new_page().goto(self.base + "/leads")
        self.active = 0
        time.sleep(0.3)

    def close(self):
        if self.ctx:
            self.ctx.close()
        self.browser.close()
        self._pw.stop()

    @property
    def pages(self):
        return self.ctx.pages

    @property
    def page(self):
        self.active = min(self.active, len(self.pages) - 1)
        return self.pages[self.active]

    # -------------------------------------------------------------- observe
    def observe(self):
        p = self.page
        try:
            p.wait_for_load_state("domcontentloaded", timeout=5000)
            raw = p.evaluate(OBSERVE_JS)
        except PWError as e:
            raw = {"elements": [], "text": f"(page unavailable: {e})", "title": "", "modal_visible": False, "loading": False}
        url = p.url
        obs = dict(raw)
        obs["url"] = url
        obs["url_path"] = re.sub(r"^https?://[^/]+", "", url)
        obs["tabs"] = [{"index": i, "url": re.sub(r"^https?://[^/]+", "", pg.url), "active": i == self.active}
                       for i, pg in enumerate(self.pages)]
        obs["dom_hash"] = dom_hash(obs)
        self.last_obs = obs
        return obs

    def render(self, obs):
        """Text the model sees."""
        tabs = " | ".join(f"{'*' if t['active'] else ' '}{t['index']}: {t['url']}" for t in obs["tabs"])
        return (f"URL: {obs['url_path']}\nTabs (* = active): {tabs}\n\n"
                f"Interactive elements:\n" + "\n".join(element_line(e) for e in obs["elements"]) +
                f"\n\nPage text:\n{obs['text']}")

    def screenshot(self, name):
        path = os.path.join(self.shot_dir, name)
        try:
            self.page.screenshot(path=path)
            return path
        except PWError:
            return None

    # -------------------------------------------------------------- act
    def _loc(self, el_id):
        loc = self.page.locator(f'[data-fb-id="{int(el_id)}"]')
        if loc.count() == 0:
            raise LookupError(f"stale element: [{el_id}] is no longer on the page (it changed after the last observation)")
        return loc.first

    def act(self, action):
        """Run one action. Returns (result_text, error_text_or_None)."""
        kind = action.get("action")
        n_pages = len(self.pages)
        try:
            if kind == "click":
                self._loc(action["id"]).click(timeout=3000)
            elif kind == "type":
                self._loc(action["id"]).fill(str(action["text"]), timeout=3000)
            elif kind == "select":
                loc = self._loc(action["id"])
                try:
                    loc.select_option(label=action["option"], timeout=3000)
                except PWError:
                    loc.select_option(value=action["option"], timeout=3000)
            elif kind == "check":
                loc = self._loc(action["id"])
                loc.set_checked(bool(action.get("checked", True)), timeout=3000)
            elif kind == "goto":
                path = action["path"] if action["path"].startswith("/") else "/" + action["path"]
                self.page.goto(self.base + path, timeout=10000)
            elif kind == "switch_tab":
                idx = int(action["index"])
                if not 0 <= idx < len(self.pages):
                    raise LookupError(f"no tab {idx}")
                self.active = idx
                self.page.bring_to_front()
            elif kind == "wait":
                time.sleep(min(float(action.get("ms", 1000)), 5000) / 1000)
            elif kind == "done":
                return "done", None
            else:
                raise ValueError(f"unknown action {kind}")
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            except PWError:
                pass
            time.sleep(0.25)
            note = ""
            if len(self.pages) > n_pages:
                note = f" A new tab opened (tab {len(self.pages) - 1}); you are still on tab {self.active}."
            return "ok." + note, None
        except PWTimeout as e:
            msg = str(e).split("\n")
            detail = next((l.strip() for l in msg if "intercepts pointer events" in l), msg[0])
            return None, f"timeout: {detail}"
        except (LookupError, ValueError, KeyError) as e:
            return None, str(e)
        except PWError as e:
            return None, f"browser error: {str(e).splitlines()[0]}"
