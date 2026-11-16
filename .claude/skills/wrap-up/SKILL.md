---
name: wrap-up
description: Close out a working session on this project by bringing docs/STATUS.md and docs/DECISIONS.md up to date and committing. Use when Kyle says he is done for now, asks to save progress, or before ending a session that changed anything.
---

# Wrap up a session

1. **Check the work.** Run `uv run pytest` and `uv run ruff check`. Report failures plainly;
   do not record something as done if its checks fail.
2. **Rewrite `docs/STATUS.md` to match reality.**
   - It holds the current state only: where things stand, next, later, known unknowns.
   - Move finished items out rather than ticking them; git has the history.
   - Remove unknowns that were answered and add new ones.
   - Update the "Last updated" date. Keep it under about 100 lines.
3. **Append to `docs/DECISIONS.md`** for every decision made or reversed this session:
   `D-NNNN — title`, date, the decision, why, what was rejected. Never edit old entries; add a
   one-line _"→ See D-NNNN"_ pointer under one that was superseded.
4. **Update the reference notes** (`docs/reference/`) with anything newly verified about
   pylitterbot or AWTRIX NG, and with anything that turned out to be wrong.
5. **Keep `docs/states.md` true** if any state's trigger or meaning changed.
6. **Commit** on `main` with a message that says what changed and why. Do not push.
7. **Tell Kyle** in a few lines: what is now true, what was not finished or not verified, and
   the single most useful next step.
