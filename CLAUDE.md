# skillharness

A runtime for Agent Skills (the open `SKILL.md` standard, agentskills.io) outside Claude Code: run a
skill folder on any model, with sandboxed commands, a hard budget, a typed event stream and the whole
run recorded and costed. Python, Pydantic AI underneath. Extracted from the product-unfold backend,
which is its first consumer.

## Read first

`docs/plan-v1.md` is the design. Do not reopen its decisions without saying so.

## Repositories

- This folder is the private working repository. Daily work goes on `dev`; `main` is the clean story.
- The public twin on GitHub (created at the first release) receives `main` only, every few days, by a
  plain `git push public main`. product-unfold depends on version tags in the public twin.
- Commit and push only when asked. Commits carry the author's name only: no co-author trailer.

## Working rules

- Expand every acronym on first use.
- Disk is nearly full: check sizes before adding a dependency; keep the install lean. Reuse the
  packages product-unfold already has (pydantic-ai-slim, obstore, modal) so uv hardlinks them.
- Never write secrets into the repo. Provider keys live in a git-ignored `.env` or in Modal secrets.
- The unfold skill is private and is not an example here. The example skill is small and MIT-safe.
- Default model for runs: Claude Opus 5.5 (`claude-opus-5-5`). Say the cost before any paid run.
