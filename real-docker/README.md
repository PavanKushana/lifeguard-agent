# Real Docker mode

The public demo runs on a simulated server, so nobody can break anything real. This folder
proves the same agent works on **real containers**: a small shop (nginx, a Python API,
PostgreSQL, Redis) that you break on purpose, and Lifeguard-Agent finds the cause and fixes it
for real, after your yes.

Same agent, same prompts, same safety check, same approval. Only the server is real:
`app/docker_tools.py` runs real, read-only commands (`docker inspect`, `docker logs`,
`docker stats`, `df`, `du`, `crontab -l`, plain HTTP calls), and prints them in the same columns
as the simulator, so the agent can't tell the difference.

## What it can and can't do

- **Reads only**, until you approve a fix. Every command runs without a shell.
- **Fixes:** restart a container, roll back to an older image that is on the machine, change
  a memory limit (`docker update`). Moving files and turning off jobs stay in the simulator:
  on a real machine they would touch the server itself, so the safety check blocks them here.
- Every fix is written to `changes.log` (the shop's deploy history) with who approved it.
- The shop only listens on `127.0.0.1`, so nobody on the internet can reach it.

## The 3 faults

| Command | What happens |
|---|---|
| `bash real-docker/break.sh bad-deploy` | Deploys api 2.3.2. It needs a new setting nobody filled in, so it stops at once and keeps restarting. |
| `bash real-docker/break.sh cache-crash` | Redis crashes with a real segfault (signal 11). It has no restart policy, so it stays down. |
| `bash real-docker/break.sh low-memory` | Someone lowered the api's memory limit to 16 MB "to save memory". It gets killed again and again. |
| `bash real-docker/break.sh reset` | Puts the whole shop back to normal. |

## Run it on a Nebius VM (about 20 minutes)

1. **Create the VM** in the Nebius console: Compute, Virtual machines, Create.
   Ubuntu 24.04, the smallest CPU-only type with at least 2 GB of memory, a public IP,
   and your SSH key. Check the price per hour before you click create.
2. **Log in:** `ssh <user>@<public-ip>` (the user name is shown on the VM's page).
3. **Install Docker** and let your user run it:
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER
   exit
   ```
   Log in again, then `docker ps` should work without sudo.
4. **Get the code and the Python packages:**
   ```bash
   git clone https://github.com/PavanKushana/lifeguard-agent.git
   cd lifeguard-agent
   sudo apt-get install -y python3-venv
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
5. **Keys:** `cp .env.example .env`, then `nano .env` and fill in `NEBIUS_API_KEY` and
   `TAVILY_API_KEY`. Nothing else is needed (no Telegram token on the VM).
6. **Start the shop:** `bash real-docker/setup.sh`. At the end it prints
   `Shop check: {"status": "ok", "version": "2.3.1"}`.
7. **Break it and run the agent:**
   ```bash
   bash real-docker/break.sh bad-deploy
   python -m scripts.run_real
   ```
   The alert comes from the shop's own health checks. Pick an option, type `y`, and the fix
   runs for real. Then the shop is checked for real (up to 75 seconds) and you get the verdict.
8. **Reset** between runs: `bash real-docker/break.sh reset`. For a clean start (a recording, say),
   empty the deploy history first: `rm real-docker/changes.log`, then `bash real-docker/break.sh reset`.
9. **When you're done, delete the VM** in the console, so it stops costing money.

Each run is saved like any other: `results/runs/` (the whole timeline) and
`results/incidents/` (the postmortem).

**Memory:** the agent here remembers only this server's own incidents (`real-docker/past_incidents.json`,
written after a fix that worked, not in git). The simulated demo's incidents are left out: in the first real
runs, a 66% match from the simulated shop ("api 2.3.2's Lua script crashed Redis") made the agent tell that
story about a real one-off Redis crash.

## First real runs (Nebius VM, 27 Oct 2026)

| Fault | Cause found | Fix | Verdict | AI time |
|---|---|---|---|---|
| bad-deploy | right: 2.3.2 needs `SHOP_DB_PASSWORD` | roll back api to 2.3.1 | FIXED | 17 s |
| cache-crash | partly: right part, but blamed api 2.3.2 (from the simulated memory, see above) | restart cache | FIXED | 29 s |
| low-memory | right: limit lowered to 16 MB, OOM kill (exit 137) | memory limit back to 512 MB, restart | FIXED | 26 s |

Full runs: `results/runs/2026-10-27_00*_real.txt`, postmortems in `results/incidents/`.

## If something goes wrong

- `permission denied ... docker.sock`: step 3 wasn't finished. Log out and in again after `usermod`.
- `none of the shop's containers exist`: run `bash real-docker/setup.sh` first.
- `The shop looks healthy`: break it first, or give your own alert:
  `python -m scripts.run_real "Customers can't log in"`.
- Port 80 is taken: another web server runs on the VM. Stop it, or change `127.0.0.1:80:80`
  in `docker-compose.yml` (and the 2 URLs in `app/docker_tools.py`).
