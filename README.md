# pde-pr-followup

Your PR review queue for the repos owned by `@chghealthcare/pde`: what you commented on and
should recheck, and what you haven't looked at yet.

```bash
python3 pde-pr-followup.py [--user LOGIN] [--include-drafts] [--json] [--serve [--port N]]
```

Requires `gh` (authenticated for the `chghealthcare` org) and `python3` 3.10+.

See `SKILL.md` for the rules (what is skipped, how PRs are classified).

`--serve` opens a live HTML page on `http://127.0.0.1:8765` (localhost only) in your default browser
(`--no-open` to skip).
