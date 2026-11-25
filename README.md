# flakebench

**Run a browser agent 5 times on the same task and it won't do the same thing 5 times. flakebench shows the step where the runs split and why.**

Browser agents usually fail on boring things: a dialog shows up late, a list re-sorts while the model is thinking, a link opens a new tab, or content loads a second after the agent looked. Pass/fail numbers hide all of that. flakebench gives you:

1. **Pipeline**: a small fake CRM (leads, inbox, compose, calendar) that injects realistic chaos from a seed and logs every injection.
2. **10 outreach tasks**, each with a checker that reads the database and grades the final state, including side effects ("you also changed 3 other leads").
3. **A runner** that runs any agent N times per task, resetting the site between runs, and logs every action, DOM hash, open tab and screenshot.
4. **Divergence analysis** that aligns failing traces against passing ones, finds the first step where they split, and labels the cause. It then checks that label against the site's chaos log, so a "modal" label means a modal really was injected.
5. **A static HTML report** with pass rate per task, variance, top causes and the screenshot at each split.

```
seed ──▶ POST /reset ──▶ agent runs ──▶ checker reads DB ──▶ trace.json
                                                               │
                         report/index.html ◀── analysis.json ◀─┘
```

## Quick start

```bash
pip install -r requirements.txt
playwright install chromium            # skip if you already have a Chromium (set CHROMIUM_PATH)

# no API key needed: a scripted "RPA macro" baseline
python runner/run.py --agent scripted --out runs/scripted

# a Claude agent (Opus 5.5 by default; try --model claude-sonnet-5-5 or claude-haiku-5-5)
export ANTHROPIC_API_KEY=...
python runner/run.py --agent claude --out runs/claude

# analysis + report
python report/build.py runs/claude     # writes runs/claude/report/index.html
```

Useful flags: `--tasks t01_qualify_priya,t08_oldest_contacted`, `--runs 10`, `--no-chaos` (control run), `--headed` (watch it), `--max-steps 25`.

## The chaos

All chaos comes from `POST /reset?seed=N`. The same seed gives the same chaos, so any failure can be replayed exactly.

| Chaos | Where | What it breaks |
| --- | --- | --- |
| "What's new" modal, appears 0.4 to 1.8 s after load | any page, 30% of loads | clicks get intercepted |
| Live re-sort by recent activity, 0.6 to 1.6 s after load | leads list, 35% | element handles from the last observation go stale |
| Lead links open in a new tab | leads list, 40% | agent keeps acting on the old tab |
| Inbox loads late, 0.2 to 2.5 s | inbox | agent acts on "Loading…" |
| 47 leads, 10 per page | leads list | target is on page 3 and never found |

Override rates per run: `POST /reset?seed=3&modal=0.6&reorder=0`. `GET /api/chaos` returns the log.

## The tasks

| Id | Task | What makes it hard |
| --- | --- | --- |
| t01 | Move Priya Raman to Qualified | pagination |
| t02 | Assign every Helix Robotics lead to Riley | 3 leads on different pages |
| t03 | Reply to Dana's pricing email, mention Thursday | slow inbox |
| t04 | Email the VP Engineering at Quartz Health | near-duplicate lead (Felix vs Felicity Wagner) |
| t05 | Create "Demo with Arcadia" on Dec 3 at 14:00, linked to the lead | form with date, time and lead picker |
| t06 | Archive every noreply newsletter, keep everything else | slow inbox, multi-select, don't over-archive |
| t07 | Mark Tomás Ortega Lost with a note | pagination, accented name |
| t08 | Assign the Contacted lead with the oldest activity to Riley | reasoning over a re-sorting list |
| t09 | Move "Sync with Lumen" from Dec 2 to Dec 4 | edit, don't duplicate |
| t10 | Note "sent deck" on whoever asked about the Q1 rollout | inbox to CRM lookup |

## How the divergence analysis works

Each step is keyed by `(action, target label, url, errored?)`. For each failing run, the split point is the first step whose key prefix matches no passing run of the same task. The cause is read at that step:

- **modal**: the click was intercepted, or the modal was visible when the agent acted
- **stale element**: the element numbered in the agent's last observation no longer exists
- **wrong tab**: a new tab opened earlier and the agent is not on it
- **slow render**: the page was still loading
- **pagination**: the agent gave up without finding the target
- **step limit**: the agent looped until it ran out of steps
- **agent logic**: none of the above; the model simply chose wrong (e.g. emailed Felicity instead of Felix)

Environmental labels are then checked against the chaos log for that run.

## Layout

```
site/       Flask + SQLite CRM, chaos injection, POST /reset
tasks/      10 tasks + checkers
agents/     browser env (numbered-element observation), scripted baseline, Claude agent
runner/     N runs per task, traces + screenshots
analysis/   divergence + cause labeling
report/     static HTML report
```
