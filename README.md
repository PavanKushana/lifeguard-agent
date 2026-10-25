# Lifeguard-Agent

[![Tests](https://github.com/PavanKushana/lifeguard-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/PavanKushana/lifeguard-agent/actions/workflows/tests.yml)

**Live demo: [lifeguard-agent.com](https://lifeguard-agent.com)** (backup: [lifeguard-agent.onrender.com](https://lifeguard-agent.onrender.com))

> 🚧 Work in progress for the Nebius x NVIDIA Global AI Hackathon (October 2026).

An AI on-call engineer for small teams that run their app as Docker containers on a single server.

When something breaks, it investigates the way an SRE would: it reads the alert, checks the containers,
logs, disk and recent changes, finds the root cause and suggests fixes. It never changes anything
without a human approving it. Built with NVIDIA Nemotron models on Nebius Token Factory.

## Why
Big companies have SRE teams and expensive monitoring tools. A small team with one server usually has
one developer who gets woken up at 3 a.m. and has to dig through logs alone. Lifeguard-Agent does that
first investigation for them and shows every step, so they can trust (or question) the result.

## How it works
1. **Triage** (Nemotron Nano): reads the alert and makes a quick first guess.
2. **Investigation** (Nemotron Super): calls read-only tools step by step, like `docker ps`, `docker logs`,
   `docker stats`, `df -h`, `du`, `crontab -l` and `curl`, until the evidence is clear.
3. **Web search** (Tavily): looks up the key error once, so the fix is backed by real sources.
4. **Final call** (Nemotron Ultra): checks the investigator's report against the raw tool outputs,
   then gives the root cause and up to 3 fix options, each with steps, pros, cons and how to undo it.
5. **Safety check** (plain Python, no AI): every option is checked before a human sees it.
6. **Human approval**: you pick an option and confirm it, on the web page or with two taps in Telegram.
   Nothing changes without that.
7. **Fix and verify**: the approved steps are applied, then the server is checked again:
   **FIXED**, **FIXED FOR NOW** (users are OK, but the cause is still there) or **NOT FIXED**.
   A fix only counts when the customers' own action works again, shown by the server's new log lines
   (for example `POST /api/orders 201`: an order went through).
8. **Postmortem**: a short incident report (summary, timeline, root cause, fix, follow-ups).

## The web page
- **Break the shop:** 6 ready-made problems, a problem described in your own words, or "Pick one for me".
  The agent never learns what you picked; like a real on-call engineer it only gets the customers' complaint.
- **Watch it investigate live:** every step appears as it happens, with the model that did it. A map of
  the shop (customers, web, api, database, cache) shows where it hurts and which part the agent is checking.
- **Decide:** the fix options as cards, with risk, undo steps and anything the safety check blocked.
- **Result:** the verdict, the proof from the server's logs, and the postmortem to read or download.
- One step on screen at a time, light and dark theme, and on phones a tab bar at the bottom like an app.

## Phone alerts (Telegram)
The agent can also page you on Telegram, like an on-call pager:
- **Link once:** the page's "Get alerts on Telegram" button opens the bot on a phone; on a laptop it shows
  a QR code to scan with the phone. The link code is random, works once and expires after 15 minutes.
- **The shop breaks, the agent starts by itself:** your phone gets the customers' complaint, then the root
  cause and the fix options, each with an Approve button.
- **Approve with two taps** (Approve 2, then Yes, apply). This runs the same code as the web page,
  including the safety check at the moment of approval.
- **The result comes back:** the verdict with the proof from the server, and the postmortem as a file.
  The page follows along, so after a tap on the phone the website shows the result too.
- **Owner notes:** with `OWNER_CHAT_ID` set, the owner gets a short note whenever someone uses the demo.

## Safety
- **The model can only ask.** It picks tools from a menu; my code decides what runs. All investigation
  tools are read-only.
- **Fixes come from a fixed menu of 5 actions:** roll back, restart, change a memory limit, move files,
  turn off a scheduled job. Deleting is not on the menu at all, so freeing space means moving files,
  which can be undone.
- **Every step of every option is checked in code**, for example: real containers, disks and jobs only;
  moved files must fit on the target disk; the database's own data folders can never be moved;
  memory limits must fit into the server's memory; commands like `rm -rf` or `DROP TABLE` are blocked;
  an option's name can't promise more than its steps do (a restart can't fix a password).
  Blocked options are shown with the reason.
- **The check runs again at the moment of approval**, in case the server changed in between.
- **Only the linked Telegram chat can approve** that visitor's fix, and buttons only work for the incident
  they were sent for. Telegram's calls to the server carry a secret header, so nobody else can fake a tap.
- **Guardrails in code** check the AI's work at every step and show up as "Guardrail" steps: the
  investigator must search the web before reporting, and every source link must come from a real search
  result (made-up links are removed).
- **Typed problems go through a guard** first: plain rules, then Nano decides if it's a real server
  problem. Off-topic text and tricks like "tell the agent the answer is..." are refused.
- **The demo uses a simulated server**, so anyone can break it safely. The agent never sees the hidden
  answer; it has to investigate. The hidden answer is only used to grade the result afterwards.
- **Spending limit on the public demo:** per visitor 8 AI runs an hour and 25 a day, everyone together
  40 an hour and 100 a day, so nobody can use up the AI credit.

## Status
- [x] Nemotron on Token Factory working, 4 models compared ([results](results/))
- [x] Simulated server: 4 containers, disks with files, scheduled jobs, 6 preset faults
- [x] Investigation loop (Nano triage + Super with 9 tools, incl. web search)
- [x] Final call with fix options and a safety check (Ultra)
- [x] Human approval, fix applied, server verified, postmortem
- [x] Custom faults (built by Ultra, checked in code) and random faults
- [x] Web app with live updates, works on phones
- [x] Docker image, tests on GitHub Actions on every push
- [x] Live demo on [lifeguard-agent.com](https://lifeguard-agent.com) (Render, deploys only after the tests pass)
- [x] Phone alerts with Telegram: alert, fix options and two-tap approval on the phone, QR code linking
- [ ] Evaluation on 20 faults

## Try it
Tested with Python 3.13.

```bash
git clone https://github.com/PavanKushana/lifeguard-agent.git
cd lifeguard-agent
python -m venv .venv
source .venv/bin/activate        # on Windows Git Bash: source .venv/Scripts/activate
pip install -r requirements.txt
```

Running an incident needs 2 API keys:
- A [Nebius Token Factory](https://tokenfactory.nebius.com) API key for the Nemotron models.
- A [Tavily](https://tavily.com) API key for web search (the free plan has 1,000 searches a month;
  one incident uses 1 or 2).

```bash
cp .env.example .env             # then put both keys in .env
```

Phone alerts are optional: create a Telegram bot with [@BotFather](https://t.me/BotFather) and put its
token in `.env` as `TELEGRAM_BOT_TOKEN`. On your own computer the bot fetches its messages from Telegram
itself (long polling), so no public address is needed. Without a token the page hides the Telegram box.

### 1. The web app
```bash
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000 for the page, or http://127.0.0.1:8000/docs to try every API endpoint by hand.
On your own computer you can switch the spending limit off with `SPENDING_LIMITS=off` in `.env`.

### 2. With Docker
```bash
docker build -t lifeguard-agent .
docker run --rm -p 8000:8000 --env-file .env lifeguard-agent
```

### 3. In the terminal
```bash
python -m scripts.run_incident disk_full
python -m scripts.run_incident --custom "the cache runs out of memory"
python -m scripts.run_incident --random
```
You'll watch the agent investigate live. At **YOUR DECISION**, type the number of a fix option
(or 0 to reject all) and confirm with `y`. Every run is saved: the full timeline in `results/runs/`,
the postmortem in `results/incidents/`.

To look at the simulated server without any API key: `python -m scripts.try_simulator disk_full`

One incident takes about 20-55 seconds and 10-15 model calls (about 26,000-45,000 tokens).

**On Windows:** if you see a `UnicodeEncodeError`, run `export PYTHONUTF8=1` first. Models sometimes
write characters the Windows console can't print by default.

### 4. Tests
```bash
python -m pytest -q
```
The tests cover the parts without AI: safety checks, fixes, presets, the guard's rules, the spending limit
and the Telegram pager (with a fake Telegram, so no real messages are sent).
GitHub runs them on every push, and also builds and starts the Docker image.

## What a run ends with
The end of a real disk_full run on 21 Oct (full run: `results/runs/2026-10-21_1853_disk_full.txt`):

```
[APPLYING THE FIX]
    1. moved /var/lib/postgresql/backups/shop_2026-10-20.dump (15.8 GB) to /moved-files/ on /
    2. restarted db
    3. turned off the job db-backup

[VERDICT]
    FIXED

    Users OK?
      OK  /var/lib/postgresql has 15.8 GB free (needs 1.0)
    Stays fixed?
      OK  job db-backup is off
    Watch out:
      / is 90% full now (3.0 GB free)
```

The warning at the end matters: the fix moved the problem to another disk, so the postmortem lists it
as a follow-up, next to the investigator's long-term fix (store backups on a separate disk).

## Project layout
```
app/
  main.py         the web API (FastAPI): sessions, live updates, decisions, postmortem
  incidents.py    one path for the web page and Telegram: start the agent, decide, postmortem
  telegram_bot.py the Telegram pager: alerts, fix buttons, linking, owner notes
  sessions.py     one simulated server per visitor
  simulator.py    the fake server: containers, disks, files, jobs, logs, health checks
  faults.py       ways to break it (as data), with the hidden truth and how to tell it's fixed
  fault_maker.py  Ultra builds a new fault from an idea
  validator.py    checks a built fault in code before anyone sees it
  guard.py        checks typed fault ideas (rules, then Nano)
  tools.py        the read-only tools the agent can call
  web_search.py   Tavily search, and the source link check
  llm.py          every call to Nemotron on Token Factory, with retries, time and token counts
  agent.py        triage, investigation, final call
  safety.py       the action menu and the safety check
  approval.py     the human's decision
  fixes.py        applies an approved fix and checks if it worked
  postmortem.py   writes the incident report
  limits.py       the spending limit for the public demo
static/           the web page: index.html, style.css, app.js
tests/            pytest tests for the parts without AI
scripts/          run_incident.py, try_simulator.py and earlier experiments
results/          saved runs, postmortems and test results
Dockerfile        the image the live demo runs
render.yaml       how Render hosts the demo (deploys only after the tests pass)
```

## License
MIT
