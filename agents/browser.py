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


def _detached(msg):
    # "waiting for locator" with nothing after it: the element vanished between our
    # lookup and the click (a re-render), so Playwright waited for it to come back.
    vanished = "waiting for locator" in msg and "resolved to" not in msg
    return "detached" in msg or "not attached" in msg or vanished


def dom_hash(obs):
    """Structural fingerprint of the page: element kinds and labels, no ids."""
    sig = obs["url_path"] + "\n" + "\n".join(
        f"{e['tag']}:{e['label']}:{e.get('value', '')}" for e in obs["elements"])
    return hashlib.sha1(sig.encode()).hexdigest()[:10]


COVER_JS = r"""
(el) => {
  // Is the element's center covered by something else? If so, describe the cover
  // and find a dismiss control inside the dialog that covers it.
  el.scrollIntoView({block: 'center', inline: 'center'});
  const r = el.getBoundingClientRect();
  const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  if (!top || top === el || el.contains(top) || top.contains(el) && top.tagName === 'LABEL') return null;
  const dlg = top.closest('[role=dialog], [aria-modal=true], .modal, .overlay') || top;
  // only non-committal labels: never 'OK', 'Confirm', 'Delete' or anything that could act on data
  const words = /^(dismiss|close|got it|no thanks|not now|maybe later|×)$/i;
  const btn = [...dlg.querySelectorAll('button, [role=button], a')].find(b => words.test((b.innerText || b.getAttribute('aria-label') || '').trim()));
  if (btn) btn.setAttribute('data-fb-dismiss', '1');
  return {cover: (dlg.getAttribute('aria-label') || dlg.innerText || dlg.tagName).trim().slice(0, 80), dismiss: !!btn};
}
"""

SETTLE_JS = r"""
() => new Promise(resolve => {
  // resolve once nothing is aria-busy and the DOM has been quiet for 300 ms (max 6 s)
  const t0 = performance.now();
  let last = performance.now();
  const mo = new MutationObserver(() => { last = performance.now(); });
  mo.observe(document.body, {subtree: true, childList: true, attributes: true, characterData: true});
  const tick = () => {
    const busy = document.querySelector('[aria-busy=true]');
    const now = performance.now();
    if ((!busy && now - last > 300) || now - t0 > 6000) { mo.disconnect(); resolve(now - t0); }
    else setTimeout(tick, 50);
  };
  tick();
})
"""

# Guards are the "fix" half of the demo. Each one targets one failure class the
# analysis found, and each can be switched on alone to measure its effect.
ALL_GUARDS = ("settle", "reresolve", "overlay", "follow_tab")


class BrowserEnv:
    def __init__(self, base_url, headless=True, screenshot_dir=None, guards=()):
        self.base = base_url.rstrip("/")
        self.headless = headless
        self.shot_dir = screenshot_dir
        self.guards = set(guards)
        self.guard_events = []
        self.last_error_log = None
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
        self.guard_events = []
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
            if "settle" in self.guards:
                waited = p.evaluate(SETTLE_JS)
                if waited > 400:
                    self.guard_events.append({"guard": "settle", "detail": f"waited {waited:.0f} ms for the page to settle"})
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
            self.page.screenshot(path=path, type="jpeg", quality=60)
            return path
        except PWError:
            return None

    # -------------------------------------------------------------- act
    def _fingerprint(self, el_id):
        if not self.last_obs:
            return None
        e = next((e for e in self.last_obs["elements"] if e["id"] == int(el_id)), None)
        return e and (e["tag"], e["label"], e.get("href"))

    def _loc(self, el_id):
        loc = self.page.locator(f'[data-fb-id="{int(el_id)}"]')
        if loc.count() == 0:
            if "reresolve" in self.guards:
                found = self._refind(el_id, why="went stale")
                if found is not None:
                    return found
            raise LookupError(f"stale element: [{el_id}] is no longer on the page (it changed after the last observation)")
        return loc.first

    def _refind(self, el_id, why):
        """The page re-rendered since the agent looked. Find the same element (same
        tag, label and link) in a fresh observation, if exactly one matches."""
        fp = self._fingerprint(el_id)
        if not fp:
            return None
        old = self.last_obs
        fresh = self.observe()
        self.last_obs = old  # keep the agent's view as the reference
        matches = [e for e in fresh["elements"] if (e["tag"], e["label"], e.get("href")) == fp]
        if len(matches) != 1:
            return None
        self.guard_events.append({"guard": "reresolve", "detail": f"[{el_id}] {why}; re-found '{fp[1]}' as [{matches[0]['id']}]"})
        return self.page.locator(f'[data-fb-id="{matches[0]["id"]}"]').first

    def _clear_overlay(self, loc):
        if "overlay" not in self.guards:
            return
        try:
            info = loc.evaluate(COVER_JS)
        except PWError:
            return
        if info and info["dismiss"]:
            self.page.locator('[data-fb-dismiss="1"]').first.click(timeout=2000)
            self.guard_events.append({"guard": "overlay", "detail": f"dismissed overlay '{info['cover'][:50]}' before acting"})
            time.sleep(0.15)

    def act(self, action):
        """Run one action. Returns (result_text, error_text_or_None)."""
        kind = action.get("action")
        n_pages = len(self.pages)
        g0 = len(self.guard_events)
        self.last_error_log = None
        try:
            if kind in ("click", "type", "select", "check"):
                loc = self._loc(action["id"])
                self._clear_overlay(loc)
            if kind == "click":
                try:
                    opens_tab = loc.evaluate("e => (e.closest('a') || {}).target === '_blank'")
                except PWError:
                    opens_tab = False
                try:
                    loc.click(timeout=3000)
                except PWTimeout as e:
                    # the element was swapped out mid-click (list re-rendered): re-find it once
                    if "reresolve" not in self.guards or not _detached(str(e)):
                        raise
                    loc = self._refind(action["id"], why="was detached mid-click")
                    if loc is None:
                        raise
                    try:
                        opens_tab = loc.evaluate("e => (e.closest('a') || {}).target === '_blank'")
                    except PWError:
                        pass
                    loc.click(timeout=3000)
                deadline = time.time() + 2.0
                while opens_tab and len(self.pages) <= n_pages and time.time() < deadline:
                    self.page.wait_for_timeout(50)
            elif kind == "type":
                loc.fill(str(action["text"]), timeout=3000)
            elif kind == "select":
                try:
                    loc.select_option(label=action["option"], timeout=3000)
                except PWError:
                    loc.select_option(value=action["option"], timeout=3000)
            elif kind == "check":
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
                if "follow_tab" in self.guards:
                    self.active = len(self.pages) - 1
                    self.page.bring_to_front()
                    try:
                        self.page.wait_for_load_state("domcontentloaded", timeout=5000)
                    except PWError:
                        pass
                    self.guard_events.append({"guard": "follow_tab", "detail": f"followed the new tab {self.active}"})
                    note = f" The link opened a new tab; switched to it (tab {self.active})."
                else:
                    note = f" A new tab opened (tab {len(self.pages) - 1}); you are still on tab {self.active}."
            fixes = "".join(f" [{g['guard']}: {g['detail']}]" for g in self.guard_events[g0:])
            return "ok." + note + fixes, None
        except PWTimeout as e:
            msg = str(e).split("\n")
            keys = ("intercepts pointer events", "detached", "not attached", "not visible", "not stable", "not enabled")
            detail = next((l.strip(" -") for l in msg if any(k in l for k in keys)), msg[0])
            if _detached(str(e)) and "stale" not in detail:
                detail = "stale element: " + detail
            self.last_error_log = str(e)[:1500]
            return None, f"timeout: {detail}"
        except (LookupError, ValueError, KeyError) as e:
            return None, str(e)
        except PWError as e:
            return None, f"browser error: {str(e).splitlines()[0]}"
