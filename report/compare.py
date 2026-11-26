"""Compare several runs (e.g. agent with and without guards) on one page.

    python report/compare.py runs/scripted runs/scripted-guards runs/claude runs/claude-guards --out docs

Writes <out>/index.html plus each run's own report under <out>/<run name>/.
"""
import argparse
import html
import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from report.build import CSS, build  # noqa: E402
from analysis.diverge import CAUSES, analyze  # noqa: E402

E = html.escape


def label(meta):
    g = meta.get("guards") or []
    gl = "no guards" if not g else ("all guards" if len(g) == 4 else "guard: " + ", ".join(g))
    return f'{meta["agent"]} · {gl}' + ("" if meta.get("chaos", True) else " · chaos off")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs"))
    ap.add_argument("--reference", help="a --no-chaos run of the same agent, used as extra passing references")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    cfgs = []
    for r in args.runs:
        ref = args.reference if os.path.abspath(r) != os.path.abspath(args.reference or "") else None
        a = analyze(r, ref)
        build(r, ref)
        name = os.path.basename(os.path.normpath(r))
        dst = os.path.join(args.out, name)
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(os.path.join(r, "report"), dst)
        cfgs.append((name, label(a["meta"]), a))

    task_ids = [t["task"] for t in cfgs[0][2]["tasks"]]
    bars = "".join(
        f'<div class="row" title="{a["overall"]["passes"]}/{a["overall"]["runs"]} runs passed">'
        f'<div class="name"><a href="{E(n)}/index.html">{E(l)}</a></div>'
        f'<div class="track"><div class="fill" style="width:{100 * a["overall"]["pass_rate"]:.1f}%"></div></div>'
        f'<div class="val">{a["overall"]["pass_rate"]:.0%} ({a["overall"]["passes"]}/{a["overall"]["runs"]})'
        f'<br><span style="font-size:12px">95% CI {a["overall"]["pass_rate_ci95"][0]:.0%}–{a["overall"]["pass_rate_ci95"][1]:.0%}</span></div></div>'
        for n, l, a in cfgs)

    agents = {a["meta"]["agent"] for _, _, a in cfgs}
    short = (lambda l: l.split(" · ", 1)[1]) if len(agents) == 1 else (lambda l: l)
    head = "".join(f'<th class="n">{E(short(l))}</th>' for _, l, _ in cfgs)
    rows = []
    for tid in task_ids:
        cells = []
        for _, _, a in cfgs:
            t = next((t for t in a["tasks"] if t["task"] == tid), None)
            cells.append("<td class='n'>–</td>" if t is None else
                         f'<td class="n">{t["passes"]}/{t["runs"]}'
                         f'<div class="track" style="height:6px;margin-top:4px"><div class="fill" style="height:6px;width:{100 * t["pass_rate"]:.0f}%"></div></div></td>')
        rows.append(f'<tr><td class="mono">{E(tid)}</td>{"".join(cells)}</tr>')

    causes = [c for c in CAUSES if any(c in a["causes"] for _, _, a in cfgs)]
    crow = "".join(
        f'<tr><td>{E(CAUSES[c])}</td>' + "".join(f'<td class="n">{a["causes"].get(c, 0)}</td>' for _, _, a in cfgs) + "</tr>"
        for c in causes)

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>flakebench results</title><style>{CSS}
.row {{ grid-template-columns:minmax(220px,340px) 1fr 120px; }} td.mono {{ background:none; }} td .track {{ min-width:60px; }}</style></head><body><main>
<h1>flakebench results</h1>
<p class="sub">Same {len(task_ids)} tasks, same seeds, same injected chaos. Only the agent and the guards change. Click a configuration for its full failure report.{(" Agent: <b>" + E(next(iter(agents))) + "</b>.") if len(agents) == 1 else ""}</p>
<h2>Pass rate by configuration</h2>
<div class="card">{bars}</div>
<h2>Pass rate by task</h2>
<div class="card" style="overflow-x:auto"><table><thead><tr><th>Task</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<h2>Failure causes</h2>
<div class="card" style="overflow-x:auto"><table><thead><tr><th>Cause</th>{head}</tr></thead><tbody>{crow}</tbody></table></div>
</main></body></html>"""
    open(os.path.join(args.out, "index.html"), "w").write(page)
    print(os.path.join(args.out, "index.html"))


if __name__ == "__main__":
    main()
