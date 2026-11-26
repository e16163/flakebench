"""Build a static HTML report from runs/<dir>/analysis.json.

    python report/build.py runs/<dir>          # writes runs/<dir>/report/index.html

The report is one self-contained folder (index.html + shots/) you can open
locally, zip, or publish with GitHub Pages.
"""
import html
import json
import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from analysis.diverge import analyze  # noqa: E402
from tasks.tasks import BY_ID  # noqa: E402

E = html.escape

# Design tokens. Everything visual reads from these, so matching a new look is a token change.
FONT_LINK = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
             '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
             '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">')
FOOTER = ('<footer class="foot"><span><b>polarbench</b> · reliability testing for browser agents</span>'
          '<span>Built for Polar · <a href="https://github.com/e16163/polarbench">source</a></span></footer>')
HEADER = ('<header class="bar"><a class="wm" href="https://github.com/e16163/polarbench">polarbench</a>'
          '<a class="btn-pill" href="https://github.com/e16163/polarbench#quick-start">Run it yourself</a></header>')
CSS = """
:root { --font:"Inter Variable","Inter",ui-sans-serif,system-ui,sans-serif; --display:"Saans","Inter Variable","Inter",ui-sans-serif,system-ui,sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
  --page:#ffffff; --surface:#ffffff; --side:#fafafa; --raised:#f3f3f3; --ink:#191919; --ink2:#717171; --ink3:#a0a0a0;
  --border:#1919190f; --border2:#19191914; --grid:#19191908; --track:#f3f3f3; --code:#f3f3f3;
  --accent:#2f6eeb; --accent-soft:#ebf2ff; --bar:var(--accent); --pass:var(--accent); --fail:#e40015;
  --btn:#191919; --btn-ink:#ffffff; --r-sm:8px; --r:12px; --r-lg:16px;
  --shadow:none; --shadow-lg:0 1px 2px #1919190a,0 8px 24px #19191908; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --page:#141414; --surface:#191919; --side:#191919; --raised:#232323; --ink:#f3f3f3; --ink2:#a0a0a0; --ink3:#717171;
  --border:#ffffff0f; --border2:#ffffff1a; --grid:#ffffff0a; --track:#2d2d2d; --code:#232323;
  --accent:#4f8afb; --accent-soft:#0c1d40; --fail:#f84b4b; --btn:#f3f3f3; --btn-ink:#191919;
  --shadow-lg:0 8px 24px #00000066; } }
:root[data-theme="dark"] { --page:#141414; --surface:#191919; --side:#191919; --raised:#232323; --ink:#f3f3f3; --ink2:#a0a0a0; --ink3:#717171;
  --border:#ffffff0f; --border2:#ffffff1a; --grid:#ffffff0a; --track:#2d2d2d; --code:#232323;
  --accent:#4f8afb; --accent-soft:#0c1d40; --fail:#f84b4b; --btn:#f3f3f3; --btn-ink:#191919;
  --shadow-lg:0 8px 24px #00000066; }
* { box-sizing:border-box; }
body { margin:0; background:var(--page); color:var(--ink); font:14px/20px var(--font); letter-spacing:-.002em; -webkit-font-smoothing:antialiased; }
a { color:var(--ink); text-decoration:underline; text-decoration-color:var(--border); text-underline-offset:3px; }
a:hover { text-decoration-color:var(--ink2); }
main { max-width:1080px; margin:0 auto; padding:40px 20px 48px; }
h1 { font-family:var(--display); font-size:40px; line-height:1.04; font-weight:500; letter-spacing:-.02em; margin:0 0 14px; }
h2 { font-family:var(--display); font-size:22px; line-height:28px; font-weight:500; letter-spacing:-.01em; margin:56px 0 14px; display:flex; align-items:baseline; gap:12px; }
h2 .num { font:500 13px var(--font); color:var(--ink3); font-variant-numeric:tabular-nums; }
.label { font-size:12px; font-weight:600; letter-spacing:.16em; text-transform:uppercase; color:var(--ink2); }
.eyebrow { display:inline-flex; align-items:center; gap:8px; font-size:13px; font-weight:500; color:var(--ink2); background:var(--surface); border:1px solid var(--border2); border-radius:99px; padding:4px 12px 4px 5px; margin-bottom:18px; box-shadow:var(--shadow-lg); }
.eyebrow b { background:var(--accent-soft); color:var(--accent); font-weight:600; font-size:12px; border-radius:99px; padding:1px 8px; text-transform:uppercase; letter-spacing:.02em; }
.sub { color:var(--ink2); font-weight:500; margin:0 0 20px; max-width:720px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; }
.tile { background:var(--surface); border:1px solid var(--border2); border-radius:var(--r-lg); padding:16px 18px; box-shadow:var(--shadow); }
.tile .v { font-family:var(--display); font-size:26px; line-height:32px; font-weight:500; letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
.tile .v.s { font-size:17px; line-height:1.3; padding:6px 0; } .tile .l { color:var(--ink2); font-size:12.5px; margin-top:2px; }
.card { background:var(--surface); border:1px solid var(--border2); border-radius:var(--r-lg); padding:18px 20px; box-shadow:var(--shadow); }
.row { display:grid; grid-template-columns:minmax(200px,300px) 1fr 110px; gap:14px; align-items:center; padding:7px 0; }
.row + .row { border-top:1px solid var(--grid); }
.row .name { font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.track { background:var(--track); height:8px; border-radius:99px; position:relative; overflow:hidden; }
.fill { background:var(--bar); height:8px; border-radius:99px; }
.runs { display:flex; gap:4px; justify-content:flex-end; }
.run { width:12px; height:12px; border-radius:4px; border:1.5px solid var(--ink3); }
.run.p { background:var(--pass); border-color:var(--pass); }
.val { font-variant-numeric:tabular-nums; color:var(--ink2); font-size:12.5px; text-align:right; }
.legend { color:var(--ink2); font-size:12.5px; display:flex; gap:16px; align-items:center; margin-bottom:8px; flex-wrap:wrap; }
.legend .run { display:inline-block; vertical-align:-2px; margin-right:5px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th,td { text-align:left; padding:9px 10px; border-bottom:1px solid var(--grid); }
tbody tr:last-child td { border-bottom:0; }
th { color:var(--ink3); font-weight:500; font-size:12px; white-space:nowrap; } td.n { text-align:right; font-variant-numeric:tabular-nums; }
.fail { display:grid; grid-template-columns:320px 1fr; gap:20px; margin-bottom:12px; }
.frame { border:1px solid var(--border); border-radius:var(--r); overflow:hidden; background:var(--raised); box-shadow:var(--shadow-lg); }
.frame::before { content:""; display:block; height:22px; border-bottom:1px solid var(--border);
  background:radial-gradient(circle at 13px 11px,#ff5f57 3.5px,transparent 4px),radial-gradient(circle at 27px 11px,#febc2e 3.5px,transparent 4px),radial-gradient(circle at 41px 11px,#28c840 3.5px,transparent 4px),var(--side); }
.frame img { width:100%; display:block; }
.fail code, .mono, code { font-family:var(--mono); font-size:12px; background:var(--code); padding:1px 6px; border-radius:6px; }
.tag { display:inline-block; font-size:12px; font-weight:500; padding:2px 10px; border-radius:99px; background:var(--raised); color:var(--ink2); margin:0 6px 4px 0; }
.tag.cause { background:var(--accent-soft); color:var(--accent); }
.k { color:var(--ink2); }
details summary { cursor:pointer; color:var(--ink2); margin-top:10px; font-weight:500; }
header.bar { max-width:1080px; margin:0 auto; padding:16px 20px; display:flex; align-items:center; justify-content:space-between; }
header.bar .wm { font-weight:500; font-size:17px; letter-spacing:-.02em; text-decoration:none; }
.btn-pill { background:var(--btn); color:var(--btn-ink); border-radius:99px; padding:6px 13px; font-weight:500; font-size:14px; text-decoration:none; }
.btn-pill:hover { opacity:.88; }
.foot { max-width:1080px; margin:24px auto 0; padding:24px 20px 40px; display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; color:var(--ink3); font-size:12.5px; border-top:1px solid var(--border); }
.foot b { color:var(--ink2); font-weight:600; } .foot a { color:var(--ink2); }
@media (max-width:700px) { .row { grid-template-columns:1fr 90px; } .row .track { grid-column:1 / -1; order:3; }
  .fail { grid-template-columns:1fr; } h1 { font-size:24px; } }
"""


def bar_rows(items, max_v, fmt, right):
    out = []
    for name, v, title, extra in items:
        w = 0 if max_v == 0 else 100 * v / max_v
        out.append(f'<div class="row" title="{E(title)}"><div class="name">{E(name)}</div>'
                   f'<div class="track"><div class="fill" style="width:{w:.1f}%"></div></div>'
                   f'<div>{right(v, extra)}</div></div>')
    return "\n".join(out)


def copy_shot(src, dst):
    """Copy a screenshot as a compressed JPEG so published reports stay small."""
    if src.endswith(".jpg"):
        shutil.copy(src, dst)
        return
    try:
        from PIL import Image
        Image.open(src).convert("RGB").save(dst, "JPEG", quality=60)
    except ImportError:
        shutil.copy(src, dst)


def trace_html(f):
    items = []
    for st in f["trace"]:
        a = dict(st["action"])
        kind = a.pop("action")
        a.pop("id", None)
        desc = kind + (f' <code>{E(st["label"])}</code>' if st["label"] else "") + \
            (" " + E(json.dumps(a, ensure_ascii=False)) if a else "")
        mark = " <b>&larr; split</b>" if st["i"] == f["divergence_step"] else ""
        err = f'<br><span class="k">error: {E(st["error"][:160])}</span>' if st["error"] else ""
        gd = "".join(f'<br><span class="k">guard: {E(g)}</span>' for g in st["guards"])
        items.append(f'<li><span class="k">{E(st["url"])}</span> · {desc}{mark}{err}{gd}</li>')
    return "".join(items)


def build(run_dir, reference=None):
    a = analyze(run_dir, reference)
    rep = os.path.join(run_dir, "report")
    shots = os.path.join(rep, "shots")
    os.makedirs(shots, exist_ok=True)
    o, meta = a["overall"], a["meta"]

    # pass rate per task, with one square per run (filled = pass)
    def runs_html(_v, t):
        sq = "".join(f'<span class="run{" p" if r["passed"] else ""}" title="seed {r["seed"]}: '
                     f'{"pass" if r["passed"] else "fail: " + E(r["reason"])}"></span>' for r in t["results"])
        return f'<div class="runs">{sq}</div>'
    tasks_sorted = sorted(a["tasks"], key=lambda t: (t["pass_rate"], t["task"]))
    pr_rows = bar_rows([(t["task"], t["pass_rate"], f'{t["passes"]}/{t["runs"]} passed', t) for t in tasks_sorted],
                       1.0, None, runs_html)

    cause_items = [(a["cause_labels"][c], n, f"{n} failures", None) for c, n in a["causes"].items()]
    cause_rows = bar_rows(cause_items, max([n for _, n, _, _ in cause_items] or [0]), None,
                          lambda v, _: f'<div class="val">{v}</div>')

    table = "".join(
        f'<tr><td class="mono">{E(t["task"])}</td><td>{E(BY_ID[t["task"]].prompt)}</td>'
        f'<td class="n">{t["passes"]}/{t["runs"]}</td><td class="n">{t["pass_rate_ci95"][0]:.0%}–{t["pass_rate_ci95"][1]:.0%}</td>'
        f'<td class="n">{t["steps_mean"]} ± {t["steps_std"]}</td>'
        f'<td class="n">{t["duration_mean_s"]} ± {t["duration_std_s"]}s</td><td class="n">{t["max_tabs"]}</td></tr>' for t in a["tasks"])

    fails_html = []
    for t in a["tasks"]:
        for f in t["failures"]:
            img = ""
            if f["screenshot"] and os.path.exists(os.path.join(run_dir, f["screenshot"])):
                dst = f'{t["task"]}_s{f["seed"]}_step{f["divergence_step"]}.jpg'
                copy_shot(os.path.join(run_dir, f["screenshot"]), os.path.join(shots, dst))
                img = f'<a class="frame" style="display:block" href="shots/{dst}"><img loading="lazy" src="shots/{dst}" alt="Screenshot after the divergence step"></a>'
            conf = {True: "confirmed by chaos log", False: "not in chaos log", None: "no chaos involved"}[f["confirmed_by_chaos_log"]]
            act = json.dumps(f["action"], ensure_ascii=False)
            tgt = f' on <code>{E(f["target"]["label"])}</code>' if f.get("target") else ""
            fails_html.append(f'''
<div class="card fail"><div>{img}</div><div>
  <div><span class="tag cause">{E(a["cause_labels"][f["cause"]])}</span><span class="tag">{conf}</span>{'<span class="tag">page differed from every passing run</span>' if f.get("page_differed") else ""}</div>
  <p style="margin:8px 0 4px"><b class="mono">{E(t["task"])}</b> · seed {f["seed"]} · split at step {f["divergence_step"]} of {f["n_steps"]}
  {"" if f["had_passing_reference"] else "<span class='k'>(no passing run to compare; first error used)</span>"}</p>
  <p style="margin:4px 0"><span class="k">Action:</span> <code>{E(act)}</code>{tgt}</p>
  {f'<p style="margin:4px 0"><span class="k">Error:</span> <code>{E(f["error"])}</code></p>' if f["error"] else ""}
  {f'<p style="margin:4px 0"><span class="k">Agent said:</span> {E(f["reasoning"])}</p>' if f["reasoning"] else ""}
  <p style="margin:4px 0"><span class="k">Checker:</span> {E(f["checker_reason"])}</p>
  <p style="margin:4px 0" class="k">Chaos this run: {E(", ".join(f["chaos_seen"]) or "none")}</p>
  <details><summary>Full trace ({f["n_steps"]} steps)</summary><ol start="0" style="font-size:13px;padding-left:22px">{trace_html(f)}</ol>
  <p class="k" style="font-size:13px">Replay: <code>python runner/run.py --agent ... --tasks {E(t["task"])} --runs {f["seed"] + 1}</code> (seed {f["seed"]} is the last run)</p></details>
</div></div>''')

    top = next(iter(a["causes"]), None)
    n_guard = sum(sum(t.get("guard_events", {}).values()) for t in a["tasks"])
    guard_names = E(", ".join(meta.get("guards") or []) or "off")
    usage = meta.get("usage")
    html_out = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>polarbench report</title>{FONT_LINK}<style>{CSS}</style></head><body>{HEADER}<main>
<div class="eyebrow"><b>Report</b> {E(meta["agent"])} · {o["runs"]} runs</div>
<h1>Where this agent breaks</h1>
<p class="sub">Agent <span class="mono">{E(meta["agent"])}</span> · {o["runs"]} runs ({meta["runs"]} per task, seeds 0–{meta["runs"] - 1}) ·
chaos {"on" if meta["chaos"] else "off"} · guards: {guard_names} · {E(meta["started"])}</p>
<div class="tiles">
  <div class="tile"><div class="v">{o["pass_rate"]:.0%}</div><div class="l">runs passed ({o["passes"]}/{o["runs"]}; 95% CI {o["pass_rate_ci95"][0]:.0%}–{o["pass_rate_ci95"][1]:.0%})</div></div>
  <div class="tile"><div class="v">{o["flaky_tasks"]}/{len(a["tasks"])}</div><div class="l">tasks flaky (pass sometimes)</div></div>
  <div class="tile"><div class="v">{o["always_fail"]}</div><div class="l">tasks that never passed</div></div>
  <div class="tile"><div class="v">{n_guard}</div><div class="l">guard interventions ({guard_names})</div></div>
  <div class="tile"><div class="v s">{E(a["cause_labels"][top].split(" (")[0]) if top else "none"}</div><div class="l">top failure cause</div></div>
</div>

<h2><span class="num">01</span>Pass rate by task</h2>
<div class="card">
<div class="legend"><span><span class="run p"></span>run passed</span><span><span class="run"></span>run failed</span><span>Hover a square for the checker's reason.</span></div>
{pr_rows}
</div>

<h2><span class="num">02</span>Why runs failed</h2>
<p class="sub">Each failing run is aligned with the passing runs of the same task. The cause is read at the first step where it splits from all of them, then checked against what the site actually injected.</p>
<div class="card">{cause_rows or "<p>No failures.</p>"}</div>

<h2><span class="num">03</span>Per-task numbers</h2>
<div class="card" style="overflow-x:auto"><table>
<thead><tr><th>Task</th><th>Prompt</th><th>Passed</th><th>95% CI</th><th>Steps (mean ± sd)</th><th>Time (mean ± sd)</th><th>Max tabs</th></tr></thead>
<tbody>{table}</tbody></table></div>

<h2><span class="num">04</span>Every failure, at the step it split</h2>
{"".join(fails_html) or "<p>No failures.</p>"}
{f'<p class="sub">Tokens: {usage["input"]:,} input ({usage["cache_read"]:,} from cache), {usage["output"]:,} output.</p>' if usage else ""}
</main>{FOOTER}</body></html>"""
    path = os.path.join(rep, "index.html")
    open(path, "w").write(html_out)
    return path


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--reference", help="a --no-chaos run of the same agent, used as extra passing references")
    args = ap.parse_args()
    print(build(args.run_dir, args.reference))
