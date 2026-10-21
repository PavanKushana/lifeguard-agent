# Postmortem: The new sales report page shows an error, and the whole shop keeps going down for a minute.

**Date:** 2026-10-21 18:35  
**Result:** FIXED  
**Decision by:** pavan  
*Blameless: this is about what happened, not who.*

## Summary

Users reported: "The new sales report page shows an error, and the whole shop keeps going down for a minute." Root cause: The API 2.4.0 deployment introduced a sales-report endpoint that runs a heavy JOIN query, which consumes nearly all of the PostgreSQL container's memory (98% of 1024 MiB limit) and triggers the Linux OOM killer (signal 9), repeatedly crashing the database and causing 500/503 errors. Fix applied: Rollback API to 2.3.1. Result after checking the server: FIXED.

## Impact

- What users saw: The new sales report page shows an error, and the whole shop keeps going down for a minute.
- First error in the logs: 17:58:05, `ERROR GET /api/reports/sales 500: server closed the connection unexpectedly`
- From the first error to users OK again: about 43 minutes

## Timeline

- 17:58:05 First error in the logs
- 18:35:05 Alert received, investigation started (first guess: severity critical, category crash)
- 18:35:25 Investigator's report done (9 tool calls, 1 web search)
- 18:35:38 Final call: root cause and 3 fix options
- 18:41:13 Decision by pavan: approved option 1: Rollback API to 2.3.1
- 18:41:13 Fix applied
- 18:41:13 Server checked: FIXED

## Root cause

The API 2.4.0 deployment introduced a sales-report endpoint that runs a heavy JOIN query, which consumes nearly all of the PostgreSQL container's memory (98% of 1024 MiB limit) and triggers the Linux OOM killer (signal 9), repeatedly crashing the database and causing 500/503 errors.

Confidence: high. Evidence:
- db container restarted 3 times, status 'Up 2 minutes' (list_containers)
- db memory usage 1003/1024 MiB (98%) (get_metrics)
- db logs show 'terminated by signal 9: Killed' with the heavy JOIN query (get_logs db)
- api logs show 500/503 errors on /api/reports/sales coinciding with db crashes (get_logs api)
- recent_changes shows api 2.4.0 deployed 40 minutes ago (recent_changes)

## Fix

**Rollback API to 2.3.1**

1. `rollback(container=api, to_image=shopapp/api:2.3.1)`

How to undo: Rollback api to shopapp/api:2.4.0

What was done:
- rolled back api to shopapp/api:2.3.1

Options that were offered:
- Rollback API to 2.3.1 (recommended) (approved)
- Increase DB memory limit and restart DB
- Restart DB only (temporary relief)

## Verification

**FIXED**

Users OK?
- (one of these is enough)
- OK  api runs shopapp/api:2.3.1
- NO  db memory limit is 1024 MB (needs 1400)

Stays fixed?
- OK  api runs shopapp/api:2.3.1

## Follow-ups

- Risk of the chosen fix: Loses the new sales-report feature until query is optimized

## Sources (checked against the search results)

- https://monpg.app/blog/postgresql-oom-killer-memory-accounting
- https://github.com/sclorg/postgresql-container/issues/396
- https://postgrespro.ru/list/thread-id/1236676

## Cost

13 model calls, 30.4 s, 34,947 tokens in, 5,961 out
