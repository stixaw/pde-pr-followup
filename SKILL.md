---
name: pde-pr-followup
description: Show which open PRs in @chghealthcare/pde-owned repos the user still needs to review. Finds PDE-owned repos via CODEOWNERS, keeps open PRs that reference a Jira ticket (no Dependabot, no drafts, not the user's own), skips PRs the user already approved, lists PRs the user commented on or reviewed as "Comments" (to recheck, with a new-commits hint), PRs the user has not touched as "Open PRs", mobile-team repo PRs as "Mobile PRs", and PRs titled DO NOT MERGE as "Not ready to merge". Can also serve a live HTML page on localhost. Use when the user asks "which PRs do I need to review", "what PRs have I not looked at", "which PRs need a recheck", "my review queue", or "PR follow-up".
---

# PDE PR follow-up

Runs `pde-pr-followup.py`, which prints six sections for the logged-in `gh` user:

1. **READY TO MERGE** (very top): the user's own open PRs with `APPROVALS_NEEDED` (2) or more
   current approvals and no outstanding changes-requested.
2. **My PRs needing approvals**: the user's own PRs with fewer approvals (shows `N/2 approvals`
   and who approved). Approvals count each reviewer's latest verdict; comment-only reviews,
   bots and the author are ignored. Own drafts are skipped unless `--include-drafts`; an own PR
   titled DO NOT MERGE goes to Not ready to merge.
3. **Comments**: PRs the user commented on or reviewed but has not approved. Each line
   flags `[N new commit(s) since you]` when there are new commits since the user's last activity,
   and nothing otherwise. Lines show the repo name (no PR number), the date the user last
   commented (latest of the user's reviews and PR comments), the date of the PR's latest commit,
   and the URL. Oldest PR (by creation date) first.
4. **Open PRs**: PRs the user has not touched. Oldest PR (by creation date) first.
5. **Mobile PRs**: PRs from the mobile team's repos (`MOBILE_REPOS` in the script, currently
   `pde-mobile-app`), from either list. Oldest first.
6. **Not ready to merge** (bottom): PRs from either list whose title contains "DO NOT MERGE"
   (case-insensitive). They are removed from Comments, Open PRs and Mobile PRs (this wins over Mobile). Oldest first.

PRs the user already approved are skipped. A later "changes requested" or dismissed approval
puts the PR back in Comments.

Always excluded: Dependabot PRs, PRs without a Jira key (e.g. `PDE-123`) in the title or
branch name, drafts (unless `--include-drafts`), and (from the review lists) the user's own PRs. Bot comments
(github-actions, Copilot) don't count as activity.

## Prerequisites

`gh` authenticated with access to the `chghealthcare` org. Check first:

```bash
gh auth status
```

If it fails, stop and tell the user; don't run `gh auth login` for them (interactive).

## Running it

```bash
python3 pde-pr-followup.py                 # for the logged-in gh user
python3 pde-pr-followup.py --user <login>  # for someone else
python3 pde-pr-followup.py --include-drafts
python3 pde-pr-followup.py --json          # machine-readable
python3 pde-pr-followup.py --serve         # live page at http://127.0.0.1:8765 in your default browser (--port N, --no-open)
```

Takes about 30 seconds (it checks CODEOWNERS on ~40 repos). Present the output as-is,
keeping READY TO MERGE on top and Not ready to merge at the bottom. URLs are printed as
clickable terminal links (OSC 8) when stdout is a terminal, plain otherwise. If stderr shows `WARNING: could not list PRs for <repo>`,
tell the user the results are incomplete for that repo; don't treat it as "no PRs".

## Adapting for another team/org

`ORG`, `TEAM`, `NAME_REGEX`, and `MOBILE_REPOS`, and `APPROVALS_NEEDED` are constants at the top of the script. Everything else
(user, Jira key detection) is derived at runtime, so other people can run it unchanged.

## Live page

`--serve` starts a localhost-only server (127.0.0.1) and prints the URL. The page loads the
queue (about 30 seconds), has a Refresh button, and refreshes every 5 minutes. It opens in your default browser automatically (`--no-open` to skip). Ctrl+C stops it.

## Colors and styling

Section headers are colored (terminal and live page): READY TO MERGE green, My PRs needing
approvals blue, Comments yellow, Open PRs (needing review) red; Mobile PRs and Not ready to
merge are neutral gray. Notes such as `[1/2 approvals: …]` and `[N new commit(s) since you]`
are bold gold so they stand out. Colors only appear on a terminal; piped output is plain. The
live page uses a system font stack (Inter first) and follows light/dark mode.
