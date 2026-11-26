# polarbench

**Run a browser agent many times on the same task. See the exact step where runs split, why they split, and which fix stops it.**

An independent reliability bench for Polar-style browser agents. It is not made by or affiliated with Polar.

Browser agents rarely fail on the hard part of a task. They fail on boring things: a dialog that shows up a second late, a list that re-sorts while the model is thinking, a link that opens a new tab, a page that is still loading. A single pass/fail number hides all of that, and so does a single run. polarbench makes it visible and measurable:

- **A test site that misbehaves on purpose.** "Pipeline" is a small CRM (leads, inbox, compose, calendar). It injects realistic chaos from a seed and logs every injection, so any failure can be replayed exactly.
- **11 realistic tasks with strict checkers.** Each checker reads the database and grades the final state, including side effects ("you also reassigned Grace Liu").
- **A runner** that runs any agent N times per task and records every action, DOM hash, open tab, screenshot and full Playwright error.
- **Divergence analysis.** Failing runs are aligned with passing runs. The first step where a failing run leaves every passing path gets a cause label, and the label is checked against the site's chaos log.
- **Guards: the fixes.** Four browser-side fixes, each aimed at one failure class. They work with any model, and you can switch them on one at a time to measure each.
- **A dashboard** to start runs, watch them live and read the reports, plus a CLI and a CI gate.

![Dashboard: start runs, watch them live, compare configurations](docs/img/dashboard.jpg)

## Results so far

Full reports with every failure, its screenshot and its trace: [`docs/index.html`](docs/index.html) (published at https://e16163.github.io/polarbench/ once GitHub Pages is on for `main` / `docs`).

Scripted baseline agent (a recorded macro with simulated model latency), 11 tasks × 5 seeds, same chaos schedule in every row:

| Configuration | Passed | 95% CI | Main failure cause |
| --- | --- | --- | --- |
| No guards | 10/55 (18%) | 10–30% | blocking modal (32 of 45 failures) |
| `settle` only | 16/55 (29%) | 19–42% | blocking modal |
| `reresolve` only | 10/55 (18%) | 10–30% | blocking modal |
| `follow_tab` only | 10/55 (18%) | 10–30% | blocking modal |
| `overlay` only | 26/55 (47%) | 35–60% | wrong tab, slow render |
| **All four guards** | **55/55 (100%)** | 93–100% | none |
| Chaos off (control) | 55/55 (100%) | 93–100% | none |

What this shows:

1. **The tasks are fair.** With chaos off the same agent passes every run, so every failure above comes from the environment, not from impossible tasks or broken checkers.
2. **Failures mask each other.** Fixing only the top cause (modals) lifts the pass rate from 18% to 47%, not to 100%. Once modals stop blocking, the agent gets far enough to hit the next problem (a new tab, a slow inbox). Single-fix evaluations underestimate how much work reliability takes, and also how much a full set of fixes pays off.
3. **All of it is fixable in the browser layer.** With all four guards the agent matches its chaos-off score: 55/55. None of the guards needs a smarter model.

![Per-run report: pass rate by task, then every failure at the step where it split](docs/img/report.jpg)

The scripted agent sets a floor and proves the harness works. The interesting numbers come from LLM agents, which you can run for free (below). Real model runs: see `docs/` once they're in.

## Quick start

```bash
git clone https://github.com/e16163/polarbench && cd polarbench
pip install -r requirements.txt
python -m playwright install chromium

./polarbench ui                 # dashboard at http://localhost:8800
```

In the dashboard, pick an agent, tasks, runs per task and guards, then press **Start run**. You get a live grid of runs and the agent's current screen. When the run finishes, open the report, or tick two runs and press **Compare**.

**Reading a report.** The top line is the pass rate. Below it, each task shows one square per run (filled means it passed). "Why runs failed" groups failures by cause: a pop-up blocked a click, the page was still loading, the agent was on the wrong tab, a button moved, or the agent just chose wrong. Further down, every failed run is shown at the exact step where it went wrong, with a screenshot, the error, what the agent said, and the full step list.

The same from a terminal:

```bash
./polarbench run --agent scripted --out runs/scripted                 # free, no key
./polarbench run --agent scripted --guards all --out runs/scripted-all
./polarbench compare runs/scripted runs/scripted-all --out docs/mine  # side-by-side page
./polarbench report runs/scripted                                     # one run's failure report
```

## Models (free options first)

| Agent | Cost | How |
| --- | --- | --- |
| `gemini` | free tier | `export GEMINI_API_KEY=...` (free at aistudio.google.com/apikey). Default model `gemini-3.5-flash-lite`, paced at 14 requests/min. If the daily quota runs out, the run stops cleanly; rerun the same command with `--resume` the next day. |
| `ollama` | free, local | `ollama serve` and `ollama pull qwen3:8b`, then `--agent ollama`. Slow on a laptop, and small models score low. |
| `claude` | paid | `export ANTHROPIC_API_KEY=...`, then `--agent claude --model claude-haiku-5-5 --compact`. Default model `claude-opus-5-5`. |
| `scripted` | free | No model. A floor, and a smoke test for the harness. |

The LLM agents read the same numbered-element view of the page and use the same eight tools (`click`, `type`, `select`, `check`, `goto`, `switch_tab`, `wait`, `done`), so their results are comparable. `--compact` (always on for Gemini and Ollama) sends only the task, a one-line log of past actions and the current page. That keeps every call at about 2 to 3k tokens.

## Plug in your own agent

There are three ways in, depending on how much of the stack your agent owns.

### 1. Your agent drives its own browser (Polar, Browser Use, computer use)

No code changes. The site records what happened from its side and grades the end state.

```bash
./polarbench serve --port 5055
```

```python
import requests
ep = requests.post("http://127.0.0.1:5055/api/episode/start",
                   json={"task": "t01_qualify_priya", "seed": 3, "agent": "my-agent"}).json()
my_agent.run(url=ep["start_url"], instruction=ep["prompt"])     # any browser, any harness
print(requests.post("http://127.0.0.1:5055/api/episode/finish").json())  # {"passed": ..., "reason": ...}
```

A small recorder in every page logs clicks, edits, page loads, tabs and whether a dialog was in the way. On `finish` it writes a trace in the same format the runner uses, so `./polarbench report runs/external-<date>` gives you the divergence analysis too. `GET /api/tasks` lists tasks. See `examples/external_agent.py`.

### 2. Your agent is a policy: page in, action out

Implement two methods (full contract in `agents/base.py`) and pass the class:

```python
class MyAgent:
    name = "my-agent"
    def reset(self, task, seed): ...
    def step(self, obs, obs_text, last_result):
        return {"action": "click", "id": 12}, "why I clicked"
```

```bash
./polarbench run --agent path/to/my_agent.py:MyAgent --guards all
```

`examples/my_agent.py` is a working example.

### 3. Your own tasks

```bash
./polarbench run --tasks-file examples/my_tasks.py --tasks x01_unassign_birch
```

A task is an id, a prompt and a checker `check(con) -> (passed, reason)` over the site's SQLite DB. Helpers such as `untouched_leads` catch side effects.

### CI

`--min-pass-rate 0.9` makes `run` exit with code 1 below the bar, and `./polarbench gate runs/<dir> --min-pass-rate 0.9` checks an existing run. `.github/workflows/ci.yml` runs the tests and a small guarded scripted run on every push, and uploads the report.

## The chaos

All chaos comes from `POST /reset?seed=N`. The same seed gives the same chaos, so a failure can be replayed exactly.

| Chaos | Where | What it breaks |
| --- | --- | --- |
| "What's new" modal appears 0.4 to 1.8 s after load | any page, 30% of loads | clicks are intercepted |
| Live re-sort by recent activity, 0.6 to 1.6 s after load | leads list, 35% | elements the agent saw are re-rendered (stale) |
| Lead links open in a new tab | leads list, 40% | the agent keeps working in the old tab |
| Inbox loads late (0.2 to 2.5 s, `aria-busy`) | inbox | the agent acts on "Loading…" |
| 47 leads, 10 per page | leads list | the target is on page 4 |

Override rates per run with `POST /reset?seed=3&modal=0.6&reorder=0`. `GET /api/chaos` returns the log.

## The tasks

| Id | Task | What makes it hard |
| --- | --- | --- |
| t01 | Move Priya Raman to Qualified | pagination |
| t02 | Assign every Helix Robotics lead to Riley | 3 leads on different pages |
| t03 | Reply to Dana's pricing email, mention Thursday | slow inbox |
| t04 | Email the VP Engineering at Quartz Health | near-duplicate lead (Felix vs Felicity Wagner) |
| t05 | Create "Demo with Arcadia" on Dec 3 at 14:00, linked to the lead | date, time and lead picker |
| t06 | Archive every noreply newsletter, keep everything else | slow inbox, multi-select, don't over-archive |
| t07 | Mark Tomás Ortega Lost with a note | pagination, accented name |
| t08 | Assign the Contacted lead with the oldest activity to Riley | reasoning over a list that re-sorts |
| t09 | Move "Sync with Lumen" from Dec 2 to Dec 4 | edit, don't duplicate |
| t10 | Note "sent deck" on whoever asked about the Q1 rollout | inbox to CRM lookup |
| t11 | Note "follow up" on every person with unread mail | long-horizon: 3 lookups, 3 edits, 15+ steps |

## The guards

Each guard targets one failure class and only uses generic web signals (`aria-busy`, `role=dialog`, `target=_blank`, DOM mutations), nothing specific to this site.

| Guard | Fixes | How |
| --- | --- | --- |
| `settle` | acting before content loads | before observing, wait until nothing is `aria-busy` and the DOM has been quiet for 300 ms (max 6 s) |
| `reresolve` | stale elements | if the element the agent picked is gone, or is swapped out mid-click, find the same element again by tag, label and link, but only if exactly one matches |
| `overlay` | blocking dialogs | if something covers the target, close it, but only through a non-committal button (Dismiss, Close, Not now, ×), never OK, Confirm or Delete |
| `follow_tab` | wrong tab | when a click opens a new tab, switch to it |

Every intervention is logged in the trace and shown to the agent ("[overlay: dismissed overlay 'What's new' before acting]"), so nothing happens silently.

## How the divergence analysis works

Each step is keyed by `(action, target label, url, errored?)`. A failing run's split point is its first step that no passing run of the same task ever took. Matching is by set membership, not position, so taking a different valid route isn't flagged. Passing runs come from the same configuration, plus a chaos-off reference run if you pass `--reference`. The cause is then read at that step:

| Label | Evidence |
| --- | --- |
| modal | the click was intercepted, or a dialog was visible when the agent acted |
| stale element | the element from the agent's last observation no longer exists, or was detached mid-click |
| wrong tab | a new tab opened earlier and the agent is not on it |
| slow render | the page was still loading |
| step limit | the agent looped until it ran out of steps |
| agent logic | none of the above: the model chose wrong |

Environmental labels are checked against the chaos log for that run, and the report shows "confirmed by chaos log" or "not in chaos log". Pass rates come with 95% Wilson intervals, because 5 runs is a small sample and the report should say so.

## Limitations

- **The site is synthetic.** It is built to reproduce failure modes seen on real sites, and the guards were tested against the same site. They use only generic signals, but they need validating on real apps before anyone trusts the gains.
- **The scripted agent never re-plans,** so it exaggerates how much the environment matters. An LLM agent can notice a dialog and close it on its own. Its failure mix will differ, and measuring that is the point of the LLM runs.
- **5 runs per task is a small sample.** The confidence intervals are wide. Use `--runs 10` or more before drawing per-task conclusions.
- **Cause labels are heuristics** backed by the chaos log. Anything without an environmental trigger falls into "agent logic", which mixes reasoning errors with gaps in the heuristics.
- **Bring-your-own-browser traces** come from DOM events. They see clicks and edits, but not an agent's internal retries or keyboard shortcuts, and they have no screenshots.

## Layout

```
polarbench        CLI: ui, run, report, compare, serve, tasks, gate
site/             Flask + SQLite CRM, chaos injection, reset, episode API, page recorder
tasks/            11 tasks + checkers; register() / load_tasks_file() for your own
agents/           browser env + guards, agent interface, scripted, Claude, Gemini/Ollama
runner/           N runs per task: traces, screenshots, resume, CI gate
analysis/         divergence + cause labels + confidence intervals
report/           per-run report and comparison page (static HTML)
ui/               dashboard
examples/         custom agent, custom tasks, external agent
tests/            unit + browser end-to-end tests (pytest)
```
