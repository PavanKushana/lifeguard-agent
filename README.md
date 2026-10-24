# Lifeguard-Agent

> 🚧 Work in progress for the Nebius x NVIDIA Global AI Hackathon (October 2026). The name may still change.

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
   then gives the root cause and 2-3 fix options, each with steps, pros, cons and how to undo it.
5. **Safety check** (plain Python, no AI): every option is checked before a human sees it.
6. **Human approval**: you pick an option and confirm it. Nothing changes without that.
7. **Fix and verify**: the approved steps are applied, then the server is checked again:
   **FIXED**, **FIXED FOR NOW** (users are OK, but the cause is still there) or **NOT FIXED**.
8. **Postmortem**: a short incident report (summary, timeline, root cause, fix, follow-ups) is saved.

Every step is shown live in the terminal, with the time and token cost of each incident.

## Safety
- **The model can only ask.** It picks tools from a menu; my code decides what runs. All investigation
  tools are read-only.
- **Fixes come from a fixed menu of 5 actions:** roll back, restart, change a memory limit, move files,
  turn off a scheduled job. Deleting is not on the menu at all, so freeing space means moving files,
  which can be undone.
- **Every step of every option is checked in code**, for example: real containers, disks and jobs only;
  moved files must fit on the target disk; the database's own data folders can never be moved;
  memory limits must fit into the server's memory; commands like `rm -rf` or `DROP TABLE` are blocked.
  Blocked options are shown with the reason.
- **The check runs again at the moment of approval**, in case the server changed in between.
- **Source links are checked in code** against what the search really returned. Models sometimes change
  a character when they copy a link, so a corrected or removed link is shown as a RULE CHECK.
- **The demo uses a simulated server**, so anyone can break it safely. The agent never sees the hidden
  answer; it has to investigate. The hidden answer is only used to grade the result afterwards.

## Status
- [x] Nemotron on Token Factory working, 4 models compared ([results](results/))
- [x] Simulated server: 4 containers, disks with files, scheduled jobs, 2 preset faults
- [x] Investigation loop (Nano triage + Super with 9 tools, incl. web search)
- [x] Final call with fix options and a safety check (Ultra)
- [x] Human approval, fix applied, server verified, postmortem saved
- [ ] Custom and random faults, more presets
- [ ] Web app and live demo link
- [ ] Evaluation on 20 faults

## Try it
Needs Python 3.11 or newer.

```bash
git clone https://github.com/PavanKushana/lifeguard-agent.git
cd lifeguard-agent
python -m venv .venv
source .venv/bin/activate        # on Windows Git Bash: source .venv/Scripts/activate
pip install -r requirements.txt
```

### 1. Look at the simulated server (no API key needed)
Break the server and see what every tool shows, the same view the agent gets:

```bash
python -m scripts.try_simulator disk_full
python -m scripts.try_simulator db_out_of_memory
```

### 2. Run a full incident (needs 2 API keys)
- A [Nebius Token Factory](https://tokenfactory.nebius.com) API key for the Nemotron models.
- A [Tavily](https://tavily.com) API key for web search (the free plan has 1,000 searches a month;
  one incident uses 1 or 2).

```bash
cp .env.example .env             # then put both keys in .env
python -m scripts.run_incident disk_full
python -m scripts.run_incident db_out_of_memory
```

You'll watch the agent investigate live. At **YOUR DECISION**, type the number of a fix option
(or 0 to reject all) and confirm with `y`. Try a weaker option too: the verdict tells you honestly
if it only buys time.

One incident takes about 20-55 seconds and 10-15 model calls (about 26,000-45,000 tokens).

**On Windows:** if you see a `UnicodeEncodeError`, run `export PYTHONUTF8=1` first. Models sometimes
write characters the Windows console can't print by default.

### 3. Check what happened
Every run is saved, so you can check what the agent really did:
- `results/runs/`: the full timeline of each run (tool calls, outputs, the final call, your decision)
- `results/incidents/`: the postmortem of each run, in markdown

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
  simulator.py   the fake server: containers, disks, files, jobs, logs, health checks
  faults.py      ways to break it (as data), with the hidden truth and how to tell it's fixed
  tools.py       the read-only tools the agent can call
  web_search.py  Tavily search, and the source link check
  llm.py         every call to Nemotron on Token Factory, with time and token counts
  agent.py       triage, investigation, final call
  safety.py      the action menu and the safety check
  approval.py    the human's decision
  fixes.py       applies an approved fix and checks if it worked
  postmortem.py  writes the incident report
scripts/         run_incident.py, try_simulator.py and earlier experiments
results/         saved runs, postmortems and test results
```

## License
MIT
