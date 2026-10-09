# Core pipeline — run timings

Same `config.json` both times (5 keywords × remote/jo/om/sa × all 6 sources = 80 planned queries).

| When | Commit | `real` time | Notes |
|---|---|---|---|
| Before (baseline) | 7e1ce2e | 6m27s (387 s) | 34 unique jobs. LinkedIn profile not logged in, so the posts search stopped after its first query. Email off (no `.env`). |
| After | 4adffe8 (+ Task 11 docs) | 1m02s (62 s) | 34 unique jobs found (28 new; 6 already in the imported seen_jobs.json). LinkedIn profile not logged in, so posts were skipped after the first query. Email off, scratch data dir. Same 80 planned queries as the baseline. |
| After polite throttling | 0fc3796 | 5m46s (346 s) | Same 80-query config, scratch data dir, email off. 34 found / 28 new (same as before). Per-site random gaps (Google 8–12 s, Bayt 6–10 s, NaukriGulf 4–7 s, Indeed 3–6 s, linkedin.com 10–20 s shared by both LinkedIn sources). LinkedIn not logged in, so posts stopped after the first query. |
