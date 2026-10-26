# Fault maker: Ultra vs Super (2026-10-26)

The same 4 ideas, built once by each model, checked by validator.py (up to 2 tries each).

| Idea | Ultra | Super |
|---|---|---|
| a runaway export job runs every minute and fills the database disk | passed, 22 s, 1 tries, $0.024 | passed, 42 s, 1 tries, $0.008 |
| someone lowered the api's memory limit by mistake and it keeps getting killed | passed, 13 s, 1 tries, $0.017 | passed, 30 s, 1 tries, $0.006 |
| the web server (nginx) was killed once and didn't come back up (it has no restart policy) | passed, 12 s, 1 tries, $0.018 | passed, 40 s, 2 tries, $0.008 |
| a new nginx version for web returns errors | passed, 12 s, 1 tries, $0.016 | passed, 35 s, 1 tries, $0.008 |

Ultra: 4 of 4 passed, 15 s on average.

Super: 4 of 4 passed, 37 s on average.
