# Tavily search settings: what I tested and what I chose

Date: 20 Oct 2026

When the agent meets an error it doesn't know, it searches the web with Tavily and reads the top 3 results. Before building that tool I wanted to know which search setting gives the agent the most useful results.

## What I tested

3 real PostgreSQL errors, one for each kind of fault the agent will meet:

| Error | Log line |
|---|---|
| Disk full | `FATAL: could not write to file "pg_wal/xlogtemp.123": No space left on device` |
| Out of memory | `server process (PID 4182) was terminated by signal 9: Killed` |
| Wrong password | `FATAL: password authentication failed for user "app"` |

Each error with 3 settings, top 3 results each:

| Setting | Cost | What it does |
|---|---|---|
| basic | 1 credit | normal search |
| advanced | 2 credits | digs deeper into each page |
| basic + trusted | 1 credit | basic, but trusted sites come first (official docs, Stack Overflow, Microsoft and AWS docs, GitHub, pganalyze) and YouTube is left out |

I judged each result by one question: **would this help the agent find the cause and the fix?**

I started with one error only. After the first test I decided one example could be luck, so I added two more, and then the third setting.

## Results

| Error | basic | advanced | basic + trusted | Best |
|---|---|---|---|---|
| Disk full | a fix in the snippet (move non-WAL files, a few hundred MB is enough), the exact error on the Postgres mailing list, a Postgres-in-Docker article | a YouTube video with 9 views first, two good pages but the snippets are only intros | all trusted sources, one useful Stack Overflow comment, two snippets were page menus | **basic** |
| Out of memory | pganalyze says signal 9 usually means the Linux **OOM killer**, which is the right answer | an article about someone killing a process by hand (misleading), a raw log dump | all postgresql.org: a log dump, an email header and a source code page. Lost the pganalyze result | **basic** |
| Wrong password | two low-quality copy sites, a Stack Overflow page whose snippet was the site menu | Microsoft Learn (clear), a tutorial site, a YouTube video | Microsoft Learn: "a recent password change that hasn't been updated in the connection settings", plus Stack Overflow with real fix steps, no YouTube | **basic + trusted** |

Average time per search (first run of each setting, because Tavily caches repeated searches and the repeats came back in 0.1 s):

| Setting | Disk full | Out of memory | Wrong password | Average |
|---|---|---|---|---|
| basic | 1.8 s | 2.0 s | 3.4 s | **2.4 s** |
| advanced | 4.8 s | 5.3 s | 3.2 s | 4.4 s |
| basic + trusted | 2.6 s | 5.3 s | 2.1 s | 3.3 s |

Total cost of all tests: 24 credits out of 1,000 a month.

## What I learned

1. **Advanced never won outright.** It was the slowest, cost twice as much and still returned YouTube videos. Paying more did not mean better results.
2. **The trusted list helped once and hurt once.** It fixed the wrong-password search, but for out of memory it filled all 3 places with postgresql.org pages that had no useful text and pushed out the one result that named the real cause. Trusted sites are not the same as useful pages.
3. **The real problem is messy snippets, in every setting.** Many results were page menus, email headers, tables or raw logs. That costs tokens and gives the agent nothing.
4. **3 errors is a small test.** It is enough to rule out advanced, but not enough to prove the trusted list is better or worse.

## What I chose

- **basic depth**: won 2 of 3, fastest, 1 credit.
- **leave out YouTube**: the agent reads text, it can't watch a video.
- **clean the snippets** before the agent reads them: drop menu lines, table borders and empty lines.
- **keep the trusted list as a setting**, switched off for now. The 20-fault evaluation (Day 11) will test it again on many more errors.

## Raw results

- `tavily_comparison_2026-10-20.txt`: first test, disk full only, basic vs advanced
- `tavily_comparison_3_errors_2026-10-20.txt`: 3 errors, basic vs advanced
- `tavily_comparison_trusted_2026-10-20.txt`: 3 errors, all 3 settings
- Script: `scripts/checking_tavily.py`
