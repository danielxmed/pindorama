---
name: check
description: Run the aggregator sensor (lint + typecheck + tests) and report the result.
---

Run `bash scripts/check.sh` from the repo root and report the outcome verbatim.

If any sensor fails, do **not** silently fix it — report what failed (file:line if available) and ask whether to proceed with a fix in this turn or in a follow-up. The point of this command is feedback, not action.

If everything passes, reply with a single line: `check: green`.
