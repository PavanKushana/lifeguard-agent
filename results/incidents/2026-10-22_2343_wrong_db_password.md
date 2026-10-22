# Postmortem: The home page loads, but every product page says 'Service unavailable'.

**Date:** 2026-10-22 23:43  
**Result:** FIXED  
**Decision by:** pavan  
*Blameless: this is about what happened, not who.*

## Summary

Users reported: "The home page loads, but every product page says 'Service unavailable'." Root cause: The API deployment version 2.3.2 introduced incorrect database credentials (password or authentication method) that fail PostgreSQL's scram-sha-256 requirement, causing authentication failures for user 'shop' and 503 errors on product pages. Fix applied: Rollback API to previous version 2.3.1. Result after checking the server: FIXED.

## Impact

- What users saw: The home page loads, but every product page says 'Service unavailable'.
- First error in the logs: 23:09:17, `ERROR GET /api/products 503: database connection failed: FATAL:  password authentication failed for user "shop"`
- From the first error to users OK again: about 35 minutes

## Timeline

- 23:09:17 First error in the logs
- 23:43:17 Alert received, investigation started (first guess: severity high, category crash)
- 23:43:35 Investigator's report done (8 tool calls, 1 web search)
- 23:43:47 Final call: root cause and 3 fix options
- 23:43:54 Decision by pavan: approved option 1: Rollback API to previous version 2.3.1
- 23:43:54 Fix applied
- 23:43:54 Server checked: FIXED

## Root cause

The API deployment version 2.3.2 introduced incorrect database credentials (password or authentication method) that fail PostgreSQL's scram-sha-256 requirement, causing authentication failures for user 'shop' and 503 errors on product pages.

Confidence: high. Evidence:
- API health endpoint returns HTTP 503 while home page returns HTTP 200 (check_endpoint).
- API logs show repeated 'FATAL: password authentication failed for user "shop"' starting at 2026-10-22 23:09:17, minutes after 2.3.2 deployment (get_logs api).
- PostgreSQL logs confirm FATAL password authentication failures for user 'shop' matching pg_hba.conf line 100 (scram-sha-256) at same timestamps (get_logs db).
- Recent changes show API deployed at 2026-10-22 23:08:17 with version 2.3.2 (database settings read from environment) (recent_changes).

## Fix

**Rollback API to previous version 2.3.1**

1. `rollback(container=api, to_image=shopapp/api:2.3.1)`

How to undo: Roll forward to shopapp/api:2.3.2 after fixing credentials

What was done:
- rolled back api to shopapp/api:2.3.1

Options that were offered:
- Rollback API to previous version 2.3.1 (recommended) (approved)
- Restart API after correcting database credentials
- Rollback API to 2.3.1 and disable scheduled log rotation

## Verification

**FIXED**

Users OK?
- OK  api runs shopapp/api:2.3.1

Stays fixed?
- OK  api runs shopapp/api:2.3.1

## Follow-ups

- Risk of the chosen fix: Loses any new features/fixes introduced in 2.3.2
- Risk of the chosen fix: Requires re-deployment of fixed 2.3.2 later
- Long-term fix suggested by the investigator: Manage database credentials via a secure secret store or config map, and add a pre‑deploy validation step that checks DB connectivity before releasing a new API version.

## Sources (checked against the search results)

- https://scalablehuman.com/2024/11/21/troubleshooting-password-authentication-failures-in-docker-compose-with-postgresql
- https://stackoverflow.com/questions/74182080/docker-compose-w-postgresql-psql-password-authentication-failed
- https://www.iloveblogs.blog/post/fix-postgres-password-authentication-failed-for-user

## Cost

11 model calls, 25.9 s, 24,832 tokens in, 5,239 out
