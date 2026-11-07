# boreale-archive

Raw snapshots of short-retention space-weather forecasts and data (NOAA SWPC, GFZ),
saved exactly as published, for Boreale's forecast benchmark. See README.md for the
sources, their retention, and the layout.

## Stack
- `snapshot.py`: one Python script, standard library only
- `.github/workflows/snapshot.yml`: runs it at 05:17 and 17:17 UTC and commits the results
- Dev tools (pytest, ruff) pinned with uv in `uv.lock`; GitHub Actions pinned to commit SHAs

## Git workflow
- Every task starts from a GitHub issue. Read it with `gh issue view <number>`.
- Branch name: `<issue-number>-short-description`, created from latest main.
- Commit messages follow Conventional Commits (feat:, fix:, docs:, chore:, refactor:).
- Open PRs with `gh pr create`, fill in the template, and include "Closes #<number>"
  (or "Part of #<number>" when the PR doesn't finish the issue).
- Never push directly to main. The only exception is the snapshot workflow, which
  commits snapshots and manifest rows to main as github-actions[bot].
- Don't add "Generated with Claude Code" to PR descriptions or Co-Authored-By lines to commits.

## Working rules
- Small, focused changes; explain non-obvious code so I can learn from it
- Run `uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest` before
  saying a task is done
- Never use a URL from memory. Find it from the provider's own pages, confirm it against
  the live endpoint, and update the README sources table (with its retention).
- Archived data is append-only: never edit or delete files under `snapshots/` or
  existing rows in `manifest.csv`
- Tests make no network calls (tests/conftest.py blocks sockets)
- Pin every GitHub Action to a full commit SHA with the version in a comment
- After each PR, draft a devlog entry in docs/devlog/ named <issue-number>-short-name.md,
  following docs/devlog/TEMPLATE.md. Write "What I learned" only after I've reported my
  manual checks and any bugs they found are fixed and I've retested them. Until then,
  keep the devlog draft uncommitted and don't ask me the "What I learned" questions.
  Then ask me 2–3 short questions about what was new, surprising, or difficult for me,
  and write the section in first person from my answers. Keep my wording where possible.
- When I report the results of manual checks, update the PR description's checklist
  with `gh pr edit`: tick the boxes I confirmed and add a short note of what I tested.
  Never tick a box I haven't confirmed.
