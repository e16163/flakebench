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

CSS = """
:root { --surface:#ffffff; --page:#f6f7f9; --ink:#191919; --ink2:#666666; --grid:#e3e5e8;
  --bar:#257de0; --track:#eef1f5; --pass:#257de0; --border:#e3e5e8; --code:#f2f3f5; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --surface:#191919; --page:#111213; --ink:#efefef; --ink2:#9c9c9c; --grid:#2c2d30;
  --bar:#338ced; --track:#26282b; --pass:#338ced; --border:#2c2d30; --code:#222326; } }
:root[data-theme="dark"] { --surface:#191919; --page:#111213; --ink:#efefef; --ink2:#9c9c9c; --grid:#2c2d30;
  --bar:#338ced; --track:#26282b; --pass:#338ced; --border:#2c2d30; --code:#222326; }
* { box-sizing: border-box; }
body { margin:0; background:var(--page); color:var(--ink); font:15px/1.5 system-ui,-apple-system,sans-serif; }
main { max-width:1040px; margin:0 auto; padding:32px 16px 64px; }
h1 { font-size:26px; margin:0 0 4px; } h2 { font-size:19px; margin:36px 0 12px; }
.sub { color:var(--ink2); margin:0 0 20px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; }
.tile { background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:14px 16px; }
.tile .v { font-size:28px; font-weight:700; } .tile .v.s { font-size:18px; line-height:1.3; padding:5px 0; } .tile .l { color:var(--ink2); font-size:13px; }
.card { background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:16px; }
.row { display:grid; grid-template-columns:minmax(200px,300px) 1fr 110px; gap:12px; align-items:center; padding:6px 0; }
.row .name { font-size:13.5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.track { background:var(--track); height:14px; border-radius:0 4px 4px 0; position:relative; }
.fill { background:var(--bar); height:14px; border-radius:0 4px 4px 0; }
.runs { display:flex; gap:4px; justify-content:flex-end; }
.run { width:14px; height:14px; border-radius:3px; border:2px solid var(--pass); }
.run.p { background:var(--pass); }
.val { font-variant-numeric:tabular-nums; color:var(--ink2); font-size:13px; text-align:right; }
.legend { color:var(--ink2); font-size:13px; display:flex; gap:16px; align-items:center; margin-bottom:8px; }
.legend .run { display:inline-block; vertical-align:-2px; margin-right:4px; }
table { width:100%; border-collapse:collapse; font-size:13.5px; }
th,td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--grid); }
th { color:var(--ink2); font-weight:600; } td.n { text-align:right; font-variant-numeric:tabular-nums; }
.fail { display:grid; grid-template-columns:300px 1fr; gap:16px; margin-bottom:12px; }
.fail img { width:100%; border:1px solid var(--border); border-radius:6px; display:block; }
.fail code, .mono { font-family:ui-monospace,monospace; font-size:12.5px; background:var(--code); padding:1px 5px; border-radius:4px; }
.tag { display:inline-block; font-size:12px; padding:1px 8px; border-radius:99px; border:1px solid var(--border); color:var(--ink2); margin-right:6px; }
.k { color:var(--ink2); }
details summary { cursor:pointer; color:var(--ink2); margin-top:8px; }
@media (max-width:700px) { .row { grid-template-columns:1fr 90px; } .row .track { grid-column:1 / -1; order:3; }
  .fail { grid-template-columns:1fr; } }
"""


def bar_rows(items, max_v, fmt, right):
    out = []
    for name, v, title, extra in items:
        w = 0 if max_v == 0 else 100 * v / max_v
        out.append(f'<div class="row" title="{E(title)}"><div class="name">{E(name)}</div>'
                   f'<div class="track"><div class="fill" style="width:{w:.1f}%"></div></div>'
                   f'<div>{right(v, extra)}</div></div>')
    return "\n".join(out)


def build(run_dir):
    a = analyze(run_dir)
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
        f'<td class="n">{t["passes"]}/{t["runs"]}</td><td class="n">{t["steps_mean"]} ± {t["steps_std"]}</td>'
        f'<td class="n">{t["duration_mean_s"]} ± {t["duration_std_s"]}s</td></tr>' for t in a["tasks"])

    fails_html = []
    for t in a["tasks"]:
        for f in t["failures"]:
            img = ""
            if f["screenshot"] and os.path.exists(os.path.join(run_dir, f["screenshot"])):
                dst = f'{t["task"]}_s{f["seed"]}_step{f["divergence_step"]}.png'
                shutil.copy(os.path.join(run_dir, f["screenshot"]), os.path.join(shots, dst))
                img = f'<a href="shots/{dst}"><img loading="lazy" src="shots/{dst}" alt="Screenshot after the divergence step"></a>'
            conf = {True: "confirmed by chaos log", False: "not in chaos log", None: "no chaos involved"}[f["confirmed_by_chaos_log"]]
            act = json.dumps(f["action"], ensure_ascii=False)
            tgt = f' on <code>{E(f["target"]["label"])}</code>' if f.get("target") else ""
            fails_html.append(f'''
<div class="card fail"><div>{img}</div><div>
  <div><span class="tag">{E(a["cause_labels"][f["cause"]])}</span><span class="tag">{conf}</span></div>
  <p style="margin:8px 0 4px"><b class="mono">{E(t["task"])}</b> · seed {f["seed"]} · split at step {f["divergence_step"]} of {f["n_steps"]}
  {"" if f["had_passing_reference"] else "<span class='k'>(no passing run to compare; first error used)</span>"}</p>
  <p style="margin:4px 0"><span class="k">Action:</span> <code>{E(act)}</code>{tgt}</p>
  {f'<p style="margin:4px 0"><span class="k">Error:</span> <code>{E(f["error"])}</code></p>' if f["error"] else ""}
  {f'<p style="margin:4px 0"><span class="k">Agent said:</span> {E(f["reasoning"])}</p>' if f["reasoning"] else ""}
  <p style="margin:4px 0"><span class="k">Checker:</span> {E(f["checker_reason"])}</p>
  <p style="margin:4px 0" class="k">Chaos this run: {E(", ".join(f["chaos_seen"]) or "none")}</p>
</div></div>''')

    top = next(iter(a["causes"]), None)
    usage = meta.get("usage")
    html_out = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>flakebench report</title><style>{CSS}</style></head><body><main>
<h1>Where browser agents break</h1>
<p class="sub">Agent <span class="mono">{E(meta["agent"])}</span> · {o["runs"]} runs ({meta["runs"]} per task, seeds 0–{meta["runs"] - 1}) ·
chaos {"on" if meta["chaos"] else "off"} · {E(meta["started"])}</p>
<div class="tiles">
  <div class="tile"><div class="v">{o["pass_rate"]:.0%}</div><div class="l">runs passed ({o["passes"]}/{o["runs"]})</div></div>
  <div class="tile"><div class="v">{o["flaky_tasks"]}/{len(a["tasks"])}</div><div class="l">tasks flaky (pass sometimes)</div></div>
  <div class="tile"><div class="v">{o["always_fail"]}</div><div class="l">tasks that never passed</div></div>
  <div class="tile"><div class="v s">{E(a["cause_labels"][top].split(" (")[0]) if top else "none"}</div><div class="l">top failure cause</div></div>
</div>

<h2>Pass rate by task</h2>
<div class="card">
<div class="legend"><span><span class="run p"></span>run passed</span><span><span class="run"></span>run failed</span><span>Hover a square for the checker's reason.</span></div>
{pr_rows}
</div>

<h2>Why runs failed</h2>
<p class="sub">Each failing run is aligned with the passing runs of the same task. The cause is read at the first step where it splits from all of them, then checked against what the site actually injected.</p>
<div class="card">{cause_rows or "<p>No failures.</p>"}</div>

<h2>Per-task numbers</h2>
<div class="card" style="overflow-x:auto"><table>
<thead><tr><th>Task</th><th>Prompt</th><th>Passed</th><th>Steps (mean ± sd)</th><th>Time (mean ± sd)</th></tr></thead>
<tbody>{table}</tbody></table></div>

<h2>Every failure, at the step it split</h2>
{"".join(fails_html) or "<p>No failures.</p>"}
{f'<p class="sub">Tokens: {usage["input"]:,} input ({usage["cache_read"]:,} from cache), {usage["output"]:,} output.</p>' if usage else ""}
</main></body></html>"""
    path = os.path.join(rep, "index.html")
    open(path, "w").write(html_out)
    return path


if __name__ == "__main__":
    print(build(sys.argv[1]))
