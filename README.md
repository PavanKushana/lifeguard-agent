# Lifeguard Agent

> 🚧 Work in progress for the Nebius x NVIDIA Global AI Hackathon (October 2026). The name may still change.

An AI on-call engineer for small teams that run their app as Docker containers on a single server.

When something breaks, it investigates the way an SRE would: it reads the alert, checks the containers,
logs, disk and recent changes, finds the root cause and suggests fixes. It never changes anything
without a human clicking Approve. Built with NVIDIA Nemotron models on Nebius Token Factory.

## Why
Big companies have SRE teams and expensive monitoring tools. A small team with one server usually has
one developer who gets woken up at 3 a.m. and has to dig through logs alone. Lifeguard Agent does that
first investigation for them and shows every step, so they can trust (or question) the result.

## How it will work
1. **Triage** (Nemotron Nano): reads the alert and decides how serious it is.
2. **Investigation** (Nemotron Super): calls tools step by step, like `docker ps`, `docker logs`, `df -h`.
3. **Web search** (Tavily): checks current docs, because models can be out of date.
4. **Final call** (Nemotron Ultra): root cause, the evidence for it, and 2-3 fix options with pros and cons.
5. **Fix with approval**: a human picks a fix, the agent applies it, checks the server is healthy again,
   and writes a short postmortem.

Every step is shown live, with the time and cost of each incident.

**Safety:** the model can only *ask* for tools. My code decides what runs. Read-only checks run
automatically, changes need approval, and dangerous actions (like deleting database files) are never allowed.

**The demo uses a simulated server**, so anyone can break it safely. You can pick a preset fault,
type your own, or get a random one. The agent never sees the hidden answer; it has to investigate.

## Status
- [x] Nemotron on Token Factory working, 4 models compared ([results](results/))
- [x] Tool calling demo: the model asks for a tool, my code runs it
- [x] Simulated server with 4 containers, 6 read-only tools and 2 preset faults
- [ ] Investigation loop (Nano triage + Super with tools)
- [ ] Final call, web search, approved fixes, postmortem
- [ ] Custom and random faults
- [ ] Web app and live demo link
- [ ] Evaluation on 20 faults

## Try it (so far)
Needs Python 3.11 or newer.

```bash
git clone https://github.com/PavanKushana/lifeguard-agent.git
cd lifeguard-agent
python -m venv .venv
source .venv/bin/activate        # on Windows Git Bash: source .venv/Scripts/activate
pip install -r requirements.txt
```

Break the simulated server and look around (no API key needed):

```bash
python -m scripts.try_simulator disk_full
python -m scripts.try_simulator db_out_of_memory
```

Watch Nemotron use a tool (needs a [Token Factory](https://tokenfactory.nebius.com) API key):

```bash
cp .env.example .env             # then put your key in .env
python scripts/tool_calling_demo.py
```

## Project layout
```
app/        the product: simulated server, tools, faults (agent and web app coming)
scripts/    small scripts to try things out
results/    saved test results
```

## License
MIT
