#!/usr/bin/env python3
"""
Usage: ./pde-pr-followup.py [--user LOGIN] [--include-drafts] [--json]

Shows which open PDE PRs you still need to look at.

  1. Finds non-archived chghealthcare repos owned by @chghealthcare/pde (name-shortlist, then
     CODEOWNERS verification -- same approach as pde-check-packages).
  2. Keeps open PRs that reference a Jira ticket (e.g. ABC-123 in the title or branch name).
     Dependabot PRs, drafts (unless --include-drafts) and your own PRs are ignored.
  3. Classifies each PR for you (default: the logged-in `gh` user):
       - latest verdict is APPROVED          -> skipped
       - you commented / reviewed, not approved -> "Comments" (recheck)
       - no activity from you                -> "Open PRs"
"""

import argparse
import base64
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

ORG = "chghealthcare"
TEAM = "@chghealthcare/pde"
NAME_REGEX = "^(pde-|chg-work|chg-ws|chg-travel)"
CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
MOBILE_REPOS = {"pde-mobile-app"}  # mobile team repos: listed separately
JIRA_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]+-\d+\b")
PR_FIELDS = "number,title,url,author,headRefName,isDraft,createdAt,updatedAt,reviews,comments"


def run(cmd: list[str]) -> str | None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and cmd[:3] == ["gh", "pr", "list"]:
        print(r.stderr.strip()[:200], file=sys.stderr)
    return r.stdout if r.returncode == 0 else None


def current_user() -> str:
    out = run(["gh", "api", "user", "--jq", ".login"])
    if not out or not out.strip():
        sys.exit("Could not determine your GitHub login. Run `gh auth status` (or pass --user).")
    return out.strip()


def file_content(repo: str, path: str) -> str | None:
    out = run(["gh", "api", f"repos/{ORG}/{repo}/contents/{path}", "--jq", ".content"])
    if not out or not out.strip():
        return None
    try:
        return base64.b64decode(out.strip()).decode("utf-8", errors="replace")
    except ValueError:
        return None


def is_owned(repo: str) -> bool:
    for path in CODEOWNERS_PATHS:
        content = file_content(repo, path)
        if content is not None:
            return TEAM.lower() in content.lower()
    return False


def owned_repos() -> list[str]:
    out = run([
        "gh", "repo", "list", ORG, "--json", "name", "--limit", "1000", "--no-archived",
        "--jq", f'.[] | select(.name | test("{NAME_REGEX}"; "i")) | .name',
    ])
    if out is None:
        sys.exit("Could not list repos. Check `gh auth status` and org access.")
    candidates = [l for l in out.splitlines() if l]
    with ThreadPoolExecutor(8) as ex:
        flags = list(ex.map(is_owned, candidates))
    return [r for r, ok in zip(candidates, flags) if ok]


def is_bot_login(login: str) -> bool:
    return login.lower() in ("github-actions", "copilot") or "[bot]" in login.lower() \
        or login.lower().endswith("-bot")


def is_dependabot(pr: dict) -> bool:
    login = (pr.get("author") or {}).get("login", "").lower()
    return "dependabot" in login or pr["headRefName"].lower().startswith("dependabot/")


def jira_key(pr: dict) -> str | None:
    for text in (pr["title"], pr["headRefName"]):
        m = JIRA_RE.search(text)
        if m:
            return m.group(0).upper()
    return None


DNM_RE = re.compile(r"do\s*not\s*merge", re.IGNORECASE)


def link(url: str) -> str:
    """Clickable (OSC 8) hyperlink when writing to a terminal, plain URL otherwise."""
    if sys.stdout.isatty():
        return f"\033]8;;{url}\033\\{url}\033]8;;\033\\"
    return url


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def count_commits_since(pr: dict, since: datetime) -> int:
    """Commits are fetched per-PR (only for PRs you touched): too heavy for the bulk query."""
    out = run(["gh", "pr", "view", str(pr["number"]), "--repo", f"{ORG}/{pr['repo']}",
               "--json", "commits"])
    if not out:
        return 0
    return sum(1 for c in json.loads(out)["commits"] if ts(c["committedDate"]) > since)


def classify(pr: dict, user: str) -> tuple[str, dict]:
    """Return ('skip'|'comments'|'open', details)."""
    u = user.lower()
    mine_reviews = [r for r in pr["reviews"]
                    if (r.get("author") or {}).get("login", "").lower() == u
                    and r["state"] != "PENDING"]
    mine_comments = [c for c in pr["comments"]
                     if (c.get("author") or {}).get("login", "").lower() == u]

    verdicts = sorted((r for r in mine_reviews
                       if r["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")),
                      key=lambda r: r["submittedAt"])
    if verdicts and verdicts[-1]["state"] == "APPROVED":
        return "skip", {}

    times = [ts(r["submittedAt"]) for r in mine_reviews] + \
            [ts(c["createdAt"]) for c in mine_comments]
    if not times:
        return "open", {}

    last = max(times)
    new_commits = count_commits_since(pr, last)
    others = sum(
        1 for x in pr["reviews"] + pr["comments"]
        if (x.get("author") or {}).get("login", "").lower() not in (u, "")
        and not is_bot_login((x.get("author") or {}).get("login", ""))
        and ts(x.get("submittedAt") or x["createdAt"]) > last)
    state = verdicts[-1]["state"] if verdicts else "COMMENTED"
    return "comments", {"last": last, "new_commits": new_commits,
                        "new_replies": others, "state": state}


def repo_prs(repo: str) -> list[dict]:
    out = run(["gh", "pr", "list", "--repo", f"{ORG}/{repo}", "--state", "open",
               "--limit", "100", "--json", PR_FIELDS])
    if out is None:
        print(f"WARNING: could not list PRs for {repo}; results are incomplete", file=sys.stderr)
        return []
    prs = json.loads(out)
    for p in prs:
        p["repo"] = repo
    return prs


def collect(user: str, include_drafts: bool) -> dict:
    repos = owned_repos()
    with ThreadPoolExecutor(8) as ex:
        all_prs = [p for prs in ex.map(repo_prs, repos) for p in prs]

    comments, opened, mobile, not_ready = [], [], [], []
    for pr in all_prs:
        if is_dependabot(pr) or (pr["author"] or {}).get("login", "").lower() == user.lower():
            continue
        if pr["isDraft"] and not include_drafts:
            continue
        key = jira_key(pr)
        if not key:
            continue
        kind, info = classify(pr, user)
        if "last" in info:
            info["last"] = info["last"].isoformat()
        item = {"repo": pr["repo"], "number": pr["number"], "title": pr["title"],
                "url": pr["url"], "author": (pr["author"] or {}).get("login", "?"),
                "jira": key, "draft": pr["isDraft"], "createdAt": pr["createdAt"], "updatedAt": pr["updatedAt"], **info}
        if kind in ("comments", "open") and DNM_RE.search(pr["title"]):
            not_ready.append(item)
        elif kind in ("comments", "open") and pr["repo"] in MOBILE_REPOS:
            mobile.append(item)
        elif kind == "comments":
            comments.append(item)
        elif kind == "open":
            opened.append(item)

    for lst in (comments, opened, mobile, not_ready):
        lst.sort(key=lambda i: i["createdAt"])  # oldest PR first
    return {"user": user, "repo_count": len(repos), "comments": comments, "open": opened,
            "mobile": mobile, "not_ready": not_ready}


def print_text(d: dict) -> None:
    def line(i: dict, note: str = "") -> None:
        draft = " (draft)" if i["draft"] else ""
        print(f"- {i['repo']} {i['jira']} {i['title']}{draft} by {i['author']} "
              f"(opened {i['createdAt'][:10]}){note}\n  {link(i['url'])}")

    print(f"# PR follow-up for {d['user']} ({d['repo_count']} PDE repos)\n")
    print(f"## Comments ({len(d['comments'])})  -- you commented, not approved: recheck\n")
    for i in d["comments"]:
        flags = []
        if i["state"] == "CHANGES_REQUESTED":
            flags.append("you requested changes")
        if i["new_commits"]:
            flags.append(f"{i['new_commits']} new commit(s) since you")
        line(i, f"  [{'; '.join(flags)}]" if flags else "")
    for key, title in (("open", "Open PRs  -- not looked at yet"),
                       ("mobile", "Mobile PRs  -- mobile team repos"),
                       ("not_ready", "Not ready to merge  -- title says DO NOT MERGE")):
        name, _, rest = title.partition("  ")
        print(f"\n## {name} ({len(d[key])})  {rest}\n")
        for i in d[key]:
            line(i)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PR follow-up</title>
<style>
  :root { --bg:#fff; --fg:#1f2328; --muted:#656d76; --line:#d0d7de; --link:#0969da; --chip:#fff8c5; --chipfg:#7d4e00; }
  @media (prefers-color-scheme: dark) { :root { --bg:#0d1117; --fg:#e6edf3; --muted:#8b949e; --line:#30363d; --link:#58a6ff; --chip:#3b2f00; --chipfg:#e3b341; } }
  body { background:var(--bg); color:var(--fg); font:14px/1.45 -apple-system,system-ui,sans-serif; margin:0; padding:16px; max-width:1000px; }
  header { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin-bottom:8px; }
  h1 { font-size:20px; margin:0; } #status { color:var(--muted); }
  button { background:transparent; color:var(--fg); border:1px solid var(--line); border-radius:6px; padding:4px 10px; cursor:pointer; }
  h2 { font-size:16px; margin:24px 0 6px; padding-bottom:4px; border-bottom:1px solid var(--line); }
  h2 small { color:var(--muted); font-weight:400; }
  ul { list-style:none; margin:0; padding:0; } li { padding:6px 0; border-bottom:1px solid var(--line); }
  a { color:var(--link); text-decoration:none; } a:hover { text-decoration:underline; }
  .repo { font-weight:600; } .meta { color:var(--muted); }
  .chip { background:var(--chip); color:var(--chipfg); border-radius:10px; padding:1px 8px; font-size:12px; margin-left:6px; }
  .empty { color:var(--muted); padding:6px 0; }
</style></head><body>
<header><h1>PR follow-up</h1><span id="status">Loading… (about 30 seconds)</span><button id="refresh">Refresh</button></header>
<main id="main"></main>
<script>
const SECTIONS = [
  ["comments","Comments","you commented, not approved: recheck"],
  ["open","Open PRs","not looked at yet"],
  ["mobile","Mobile PRs","mobile team repos"],
  ["not_ready","Not ready to merge","title says DO NOT MERGE"],
];
const $ = (t, props={}, ...kids) => { const e = Object.assign(document.createElement(t), props); kids.forEach(k => e.append(k)); return e; };
function render(d) {
  const main = document.getElementById("main"); main.replaceChildren();
  for (const [key, name, hint] of SECTIONS) {
    main.append($("h2", {}, `${name} (${d[key].length}) `, $("small", {textContent: "— " + hint})));
    if (!d[key].length) { main.append($("div", {className:"empty", textContent:"Nothing here."})); continue; }
    const ul = $("ul");
    for (const i of d[key]) {
      const li = $("li");
      li.append($("a", {href:i.url, target:"_blank", rel:"noopener", textContent:i.repo, className:"repo"}), " ",
        $("a", {href:i.url, target:"_blank", rel:"noopener", textContent:`${i.jira} ${i.title}`}),
        $("span", {className:"meta", textContent:` by ${i.author} · opened ${i.createdAt.slice(0,10)}${i.draft ? " · draft" : ""}`}));
      if (i.state === "CHANGES_REQUESTED") li.append($("span", {className:"chip", textContent:"you requested changes"}));
      if (i.new_commits) li.append($("span", {className:"chip", textContent:`${i.new_commits} new commit(s) since you`}));
      ul.append(li);
    }
    main.append(ul);
  }
}
let busy = false;
async function load() {
  if (busy) return; busy = true;
  const st = document.getElementById("status"); st.textContent = "Loading… (about 30 seconds)";
  try {
    const r = await fetch("data"); if (!r.ok) throw new Error(await r.text());
    const d = await r.json(); render(d);
    st.textContent = `${d.user} · ${d.repo_count} PDE repos · updated ${new Date().toLocaleTimeString()}`;
  } catch (e) { st.textContent = "Failed: " + e.message; }
  busy = false;
}
document.getElementById("refresh").onclick = load;
load(); setInterval(load, 5 * 60 * 1000);
</script></body></html>"""


def serve(user: str, include_drafts: bool, port: int, open_browser: bool = True) -> int:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, ctype: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = self.path.split("?")[0]
            if path == "/":
                self._send(200, "text/html; charset=utf-8", PAGE.encode())
            elif path == "/data":
                try:
                    self._send(200, "application/json", json.dumps(collect(user, include_drafts)).encode())
                except Exception as e:  # shown in the page status line
                    self._send(500, "text/plain; charset=utf-8", str(e).encode())
            else:
                self._send(404, "text/plain", b"not found")

        def log_message(self, *a) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)  # localhost only
    url = f"http://127.0.0.1:{port}"
    print(f"Serving on {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)  # system default browser (macOS: `open`)
        print("Opened in your default browser.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", help="GitHub login to check (default: logged-in gh user)")
    ap.add_argument("--include-drafts", action="store_true")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--serve", action="store_true", help="serve a live HTML page on localhost")
    ap.add_argument("--no-open", action="store_true", help="with --serve: don't open the browser")
    ap.add_argument("--port", type=int, default=8765, help="port for --serve (default 8765)")
    args = ap.parse_args()

    user = args.user or current_user()
    if args.serve:
        return serve(user, args.include_drafts, args.port, not args.no_open)
    d = collect(user, args.include_drafts)
    if args.json:
        print(json.dumps(d, indent=2))
    else:
        print_text(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
