"""Before/after page for autopilot.

    ./polarbench autopilot report BENCH_DIR [--out docs/autopilot] [--title ...] [--note ...]

BENCH_DIR holds any of these runner/autopilot output folders:
    agent-v1      the agent on every run, site as recorded
    autopilot-v1  saved workflows replayed, same site
    agent-v2      the agent on every run, after the site redesign
    autopilot-v2  saved workflows replayed after the redesign (healing on)
"""
import argparse
import html
import json
import os
import shutil

from report.build import CSS, FONT_LINK, FOOTER, HEADER

E = html.escape
ROWS = [("agent-v1", "Agent every time", "Site as recorded"),
        ("autopilot-v1", "Autopilot", "Site as recorded"),
        ("agent-v2", "Agent every time", "After a redesign"),
        ("autopilot-v2", "Autopilot", "After a redesign")]


def _rows(d):
    p = os.path.join(d, "results.jsonl")
    return [json.loads(l) for l in open(p) if l.strip()] if os.path.exists(p) else []


def stats(d, kind):
    rows = _rows(d)
    if not rows:
        return None
    n = len(rows)
    if kind == "agent":
        calls = [r["n_steps"] for r in rows]  # one model call per step
        verified = None
        wrong_ok = None
    else:
        calls = [r["ai_calls"] or 0 for r in rows]
        verified = sum(1 for r in rows if r["verified"])
        wrong_ok = sum(1 for r in rows if r["verified"] and not r["passed"])
    return {"n": n, "passed": sum(1 for r in rows if r["passed"]), "calls": sum(calls), "per_run": sum(calls) / n,
            "zero": sum(1 for c in calls if c == 0), "time": sum(r["duration_s"] for r in rows) / n,
            "verified": verified, "wrong_ok": wrong_ok, "rows": rows}


def example_run(bench, out):
    """The first autopilot run on the changed site that healed something: shown step by step."""
    d = os.path.join(bench, "autopilot-v2")
    for r in _rows(d):
        if (r.get("counts") or {}).get("healed") and r["passed"]:
            run_dir = os.path.join(d, r["task"], f"run{r['seed']}")
            res = json.load(open(os.path.join(run_dir, "replay.json")))
            img = None
            if os.path.exists(os.path.join(run_dir, "final.jpg")):
                os.makedirs(os.path.join(out, "img"), exist_ok=True)
                img = f"img/{r['task']}-final.jpg"
                shutil.copy(os.path.join(run_dir, "final.jpg"), os.path.join(out, img))
            return res, img
    return None, None


LABEL = {"replayed": "Replayed", "auto_fixed": "Fixed for free", "healed": "Healed by AI", "skipped": "Skipped",
         "failed": "Failed", "waiting": "Waiting for OK"}


def build(bench, out, title=None, note=""):
    os.makedirs(out, exist_ok=True)
    S = {k: stats(os.path.join(bench, k), "agent" if k.startswith("agent") else "autopilot") for k, _, _ in ROWS}
    agent_name = ""
    for k in ("agent-v1", "agent-v2"):
        m = os.path.join(bench, k, "meta.json")
        if os.path.exists(m):
            agent_name = json.load(open(m)).get("agent", "")
            break
    heal_name = ""
    m = os.path.join(bench, "autopilot-v2", "meta.json")
    if os.path.exists(m):
        heal_name = json.load(open(m)).get("healer") or ""

    trs = []
    for k, who, site in ROWS:
        s = S[k]
        if not s:
            continue
        ver = "" if s["verified"] is None else f"{s['verified']}/{s['n']}" + (
            f' <span class="k">({s["wrong_ok"]} wrong)</span>' if s["wrong_ok"] else "")
        trs.append(f"<tr><td><b>{who}</b></td><td>{site}</td><td class=n>{s['passed']}/{s['n']}</td>"
                   f"<td class=n>{s['per_run']:.1f}</td><td class=n>{s['zero']}/{s['n']}</td>"
                   f"<td class=n>{s['time']:.0f}s</td><td class=n>{ver or '–'}</td></tr>")

    tiles = []
    a1, p1, a2, p2 = (S[k] for k, _, _ in ROWS)
    if a1 and p1:
        saved = 1 - p1["calls"] / max(1, a1["calls"])
        tiles.append(f'<div class="tile"><div class="v">{saved:.0%}</div><div class="l">fewer AI calls on repeat runs '
                     f'({p1["calls"]} vs {a1["calls"]})</div></div>')
    if a2 and p2:
        saved2 = 1 - p2["calls"] / max(1, a2["calls"])
        tiles.append(f'<div class="tile"><div class="v">{saved2:.0%}</div><div class="l">fewer AI calls after the redesign '
                     f'({p2["calls"]} vs {a2["calls"]})</div></div>')
        tiles.append(f'<div class="tile"><div class="v">{p2["passed"]}/{p2["n"]}</div><div class="l">autopilot correct after '
                     f'the redesign (agent: {a2["passed"]}/{a2["n"]})</div></div>')
    if p1:
        tiles.append(f'<div class="tile"><div class="v">{(a1["time"] / p1["time"]) if a1 else 0:.1f}×</div>'
                     f'<div class="l">faster than the agent per run</div></div>' if a1 else "")

    # per workflow: AI calls per run after the redesign (heals once, then free)
    per_wf = ""
    if p2:
        by = {}
        for r in p2["rows"]:
            by.setdefault(r["task"], []).append(r)
        cells = []
        for t, rs in sorted(by.items()):
            rs.sort(key=lambda r: r["seed"])
            dots = "".join(
                f'<span class="cnt{" p" if r["passed"] else " f"}" title="run {r["seed"]}: '
                f'{"pass" if r["passed"] else "fail"}, {r["ai_calls"]} AI calls'
                f'{", saved v" + str(r["saved_version"]) if r.get("saved_version") else ""}">{r["ai_calls"]}</span>'
                for r in rs)
            ag = ""
            if a2:
                ar = [r for r in a2["rows"] if r["task"] == t]
                if ar:
                    ag = f'{sum(r["n_steps"] for r in ar) / len(ar):.0f} per run, {sum(r["passed"] for r in ar)}/{len(ar)} correct'
            cells.append(f'<tr><td class="mono" style="background:none">{E(t)}</td><td><div class="cnts">{dots}</div></td>'
                         f'<td class="small">{ag}</td></tr>')
        per_wf = ("<h2><span class=num>02</span>After the redesign, AI is used once, then never again</h2>"
                  "<p class=sub>AI calls in each autopilot run on the changed site, in order. A blue box passed the task's "
                  "checker. The first run heals and saves a new version; the next ones replay it for free.</p>"
                  "<div class=card style='overflow-x:auto'><table><thead><tr><th>Workflow</th><th>Autopilot runs "
                  "(AI calls each)</th><th>Agent every time (AI calls)</th></tr></thead><tbody>"
                  + "".join(cells) + "</tbody></table></div>")

    ex, img = example_run(bench, out)
    ex_html = ""
    if ex:
        steps = []
        for s in ex["steps"]:
            notes = "".join(f"<li>{E(f)}</li>" for f in s["fixes"]) + "".join(
                f'<li class="ai">AI: {E(a)}</li>' for a in s["healed_actions"])
            chk = " &nbsp; ".join(f'<span class="{"y" if ok else "x"}">{"✓" if ok else "✗"}</span> {E(n)}'
                                  for n, ok in s["checks"])
            steps.append(f'<div class="st"><div class="n">{s["n"]}</div><div><div class="d">{E(s["desc"])}</div>'
                         f'{"<ul>" + notes + "</ul>" if notes else ""}{"<div class=chk>" + chk + "</div>" if chk else ""}</div>'
                         f'<span class="badge b-{s["status"]}">{LABEL[s["status"]]}</span></div>')
        ex_html = (f"<h2><span class=num>03</span>One run, step by step</h2><p class=sub>{E(ex['goal'])} "
                   f"On the redesigned site, with workflow v{ex['version']}. {ex['ai_calls']} AI call"
                   f"{'' if ex['ai_calls'] == 1 else 's'}; saved as v{ex.get('saved_version') or '?'}.</p>"
                   f"<div class='grid'><div class=card>{''.join(steps)}</div>"
                   + (f"<div><div class=frame><img src='{img}' alt='Final page'></div></div>" if img else "") + "</div>")

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Workflow autopilot results</title>{FONT_LINK}<style>{CSS}
.cnts {{ display:flex; gap:4px; }}
.cnt {{ min-width:24px; height:24px; border-radius:6px; display:grid; place-items:center; font-size:12px; font-variant-numeric:tabular-nums; border:1.5px solid var(--ink3); color:var(--ink2); }}
.cnt.p {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
.grid {{ display:grid; grid-template-columns:1.2fr 1fr; gap:20px; align-items:start; }} .grid > * {{ min-width:0; }}
@media (max-width:900px) {{ .grid {{ grid-template-columns:1fr; }} }}
.st {{ display:grid; grid-template-columns:24px 1fr auto; gap:12px; padding:10px 0; align-items:start; }}
.st + .st {{ border-top:1px solid var(--grid); }}
.st .n {{ color:var(--ink3); font-size:12.5px; text-align:right; }} .st .d {{ font-weight:500; }}
.st ul {{ margin:4px 0 0; padding-left:16px; color:var(--ink2); font-size:12.5px; }} .st .ai {{ color:var(--accent); }}
.chk {{ font-size:12.5px; color:var(--ink2); margin-top:4px; }} .chk .y {{ color:var(--accent); }} .chk .x {{ color:var(--fail); }}
.badge {{ font-size:12px; font-weight:500; padding:2px 10px; border-radius:99px; white-space:nowrap; background:var(--raised); color:var(--ink2); }}
.b-healed {{ background:var(--accent-soft); color:var(--accent); }} .b-auto_fixed {{ color:var(--ink); box-shadow:0 0 0 1px var(--border2) inset; }}
.b-failed {{ color:var(--fail); }}
</style></head><body>{HEADER}<main>
<span class="eyebrow"><b>Results</b> Workflow autopilot</span>
<h1 style="margin-top:14px">{E(title or "Repeat runs without paying for AI every time")}</h1>
<p class="sub">Same eleven tasks on the Pipeline test site, with pop-ups, slow loads, new tabs and re-sorting lists switched on.
Agent: <code>{E(agent_name)}</code>. Healer: <code>{E(heal_name)}</code>. "Correct" is the test site's own checker reading its
database; "verified" is autopilot's own check, which is all a real product would have. {E(note)}</p>
<div class="tiles">{''.join(t for t in tiles if t)}</div>
<h2><span class=num>01</span>The four rows</h2>
<div class="card" style="overflow-x:auto"><table><thead><tr><th>How it ran</th><th>Site</th><th>Correct</th><th>AI calls / run</th>
<th>Runs with no AI</th><th>Time / run</th><th>Verified</th></tr></thead><tbody>{''.join(trs)}</tbody></table></div>
{per_wf}
{ex_html}
<h2><span class=num>04</span>What changed in the redesign</h2>
<div class="card"><ul style="margin:0; padding-left:18px; line-height:1.8">
<li>"Next" is "Next page", "Save" is "Save changes", "Save event" is "Update event", "To" is "Recipient", "Notes" is "Internal notes"</li>
<li>Six new leads sort first, so most records are on a later page than when the workflow was recorded</li>
<li>Stage is picked with a row of buttons instead of a dropdown</li>
<li>The new-event form is hidden behind a "New event" button</li></ul>
<p class="k" style="margin-bottom:0">The first two kinds are fixed for free (tolerant matching, paging further). The last two need AI once.</p></div>
</main>{FOOTER}</body></html>"""
    open(os.path.join(out, "index.html"), "w").write(page)
    return os.path.join(out, "index.html")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="polarbench autopilot report")
    ap.add_argument("bench")
    ap.add_argument("--out")
    ap.add_argument("--title")
    ap.add_argument("--note", default="")
    a = ap.parse_args(argv)
    print(build(a.bench, a.out or os.path.join(a.bench, "report"), a.title, a.note))
