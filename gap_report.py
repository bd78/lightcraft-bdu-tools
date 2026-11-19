#!/usr/bin/env python3
"""List the LightCraft gaps and bug fixes nobody is actively working on, as a Markdown report.

Gaps come from the ranked table in docs/gaps.md of the upstream repository, in its order. A gap
is skipped when an open pull request whose title matches it had a commit in the last
--stale-days days. A gap whose matching pull requests all went quiet is kept, and those pull
requests are listed.

Fixes are open issues with a priority label (critical, then high, medium, low) and a bug-type
label (bug, regression, security, performance). An issue is skipped when it is assigned, has a
"needs info" / "in progress" / similar label, has an open pull request that will close it with a
commit in the window, or someone claimed it in a comment in the window without releasing it.

Requires Python 3.9+ and the GitHub CLI (`gh`), logged in (`gh auth status`). Standard library only.

Examples:
  python gap_report.py
  python gap_report.py --top 10 --stale-days 28 -o reports/next.md
  python gap_report.py --gaps-file ../lightcraft/docs/gaps.md
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_REPO = "storytold/lightcraft"

# Title search terms per gap, keyed by a lower-case piece of the gap's bold title. Tracker ids
# found in the gap's row (LR-…, LRC-…, KEYC-…, PERF-…) are searched as well. Extend or override
# with --terms-file (a JSON object of the same shape).
DEFAULT_TERMS: dict[str, list[str]] = {
    "camera colour": ["LR-PROF-CAMERACOLOR"],
    # Not plain "CR3" (camera-colour PRs name it) nor "C-RAW" (GitHub splits it and matches "raw").
    "cr3": ["crx", "CR3 decode", "CR3 decoding", "CR3 variant"],
    "render fidelity": ["LR-BEHAV-RENDER-FIDELITY", "render fidelity"],
    "ai masks": ["LR-MASK-SUBJECT", "LR-MASK-SKY", "LR-MASK-PEOPLE", "LR-MASK-BACKGROUND",
                 "LR-MASK-LANDSCAPE", "LR-MASK-OBJECTS", "SAM 3"],
    "compressed orf": ["ORF"],
    "high efficiency": ["High Efficiency", "JPEG XS", "HE NEF"],
    "lens profiles": ["LR-EDIT-OPTICS-PROFILE", "lens profile", "lensfun"],
    "run in ci": ["macOS CI", "GPU tests", "software adapter", "lavapipe"],
    "per-model verification": ["verify bodies", "verified bodies"],
    "enhance": ["Raw Details", "Super Resolution", "LR-EDIT-DETAIL-SUPERRES", "LR-ENH", "X-Trans denoise"],
    "content-aware": ["LR-REM-CONTENTAWARE", "content-aware", "distractions", "reflection removal", "generative"],
    "catalog migration": ["LRC-LIB-CATALOG-IMPORT", "Lightroom catalog"],
    "hdr display": ["LR-VIEW-HDR-DISPLAY", "HDR display", "EDR"],
    "print module": ["LRC-PRINT", "print"],
    "map view": ["LRC-MAP", "LRC-BOOK", "LRC-SS", "slideshow", "publish service", "book module"],
    "tethered": ["tether"],
    "video": ["LR-VID", "video"],
    "plug-in": ["plug-in", "plugin", "SDK"],
    "long-tail raw": ["SRW", "ARQ", "IIQ", "Hasselblad", "X3F", "KDC", "DCR", "GPR", "FinePix"],
    "localization": ["LR-BEHAV-LOCALIZE", "i18n", "translation", "RTL"],
    "accessibility": ["LR-BEHAV-ACCESS", "AccessKit", "screen reader"],
    "pen pressure": ["pressure", "pen tablet", "colord"],
    "brush live preview": ["brush preview", "mask refinement", "refine edge"],
    "file writes": ["AVIF", "Render to DNG", "LR-EXP-RENDERDNG"],
    "performance at scale": ["PERF"],
    "runtime coverage": ["uninstall", "NVIDIA", "DX12", "riscv64", "VRR"],
    "hdr merge": ["LR-MERGE-HDR", "HDR merge"],
    "shortcuts": ["KEYC"],
    "multi-select metadata": ["LR-META-EXIF-FULL", "multi-select metadata", "mixed metadata"],
}

# Tracker ids shared by many unrelated pull requests (every raw format uses LR-IMP-FORMATS), so
# they are not searched for automatically; the title terms above say what each gap is about.
BROAD_TRACKERS = {"LR-IMP-FORMATS"}

PRIORITY_LABELS = ["priority: critical", "priority: high", "priority: medium", "priority: low"]
FIX_LABELS = {"bug", "regression", "security", "performance"}
EXCLUDED_LABELS = {"in progress", "needs info", "duplicate", "wontfix", "invalid", "question",
                   "support", "no action needed", "fixed"}

TRACKER_RE = re.compile(r"\b(?:LRC?|KEYC|PERF|LC)-[A-Z0-9]+(?:-[A-Z0-9]+)*\b")
CLAIM_RE = re.compile(
    r"\b(?:i'?m|i am|i'?ll be)\s+(?:picking|taking|working on|looking into)\s+(?:this|it)\b"
    r"|\b(?:taking|claiming)\s+this\b|\bi'?ll\s+(?:take|pick up)\s+(?:this|it)\b|\bpr to follow\b",
    re.IGNORECASE,
)
RELEASE_RE = re.compile(r"\breleas(?:e|ing)\s+(?:my\s+)?claim\b|\bno longer working on\b|\bunclaim",
                        re.IGNORECASE)
RATE_LIMIT_RE = re.compile(r"rate limit|abuse detection|secondary", re.IGNORECASE)

PR_FIELDS = ("number title url isDraft state author { login } repository { nameWithOwner } "
             "commits(last: 1) { nodes { commit { committedDate } } }")

SEARCH_QUERY = f"""
query($q: String!) {{
  search(query: $q, type: ISSUE, first: 50) {{
    nodes {{ ... on PullRequest {{ {PR_FIELDS} }} }}
  }}
}}"""

ISSUES_QUERY = f"""
query($owner: String!, $name: String!, $label: String!, $after: String) {{
  repository(owner: $owner, name: $name) {{
    issues(first: 30, after: $after, states: OPEN, labels: [$label],
           orderBy: {{field: CREATED_AT, direction: DESC}}) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        number title url createdAt
        labels(first: 20) {{ nodes {{ name }} }}
        assignees(first: 5) {{ nodes {{ login }} }}
        reactions(content: THUMBS_UP) {{ totalCount }}
        comments(last: 10) {{ nodes {{ author {{ login }} body createdAt }} }}
        closedByPullRequestsReferences(first: 10, includeClosedPrs: false) {{ nodes {{ {PR_FIELDS} }} }}
        timelineItems(itemTypes: [CROSS_REFERENCED_EVENT], last: 25) {{
          nodes {{ ... on CrossReferencedEvent {{ willCloseTarget source {{ ... on PullRequest {{ {PR_FIELDS} }} }} }} }}
        }}
      }}
    }}
  }}
}}"""


class GhError(RuntimeError):
    pass


# ---------------------------------------------------------------- GitHub CLI access


def gh(args: list[str], stdin: str | None = None) -> str:
    try:
        proc = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", check=False)
    except FileNotFoundError as err:
        raise GhError("the GitHub CLI `gh` is not installed or not on PATH (https://cli.github.com)") from err
    if proc.returncode != 0:
        raise GhError((proc.stderr or proc.stdout).strip() or f"gh {' '.join(args)} failed")
    return proc.stdout


def graphql(query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables})
    for attempt in range(4):
        try:
            data = json.loads(gh(["api", "graphql", "--input", "-"], stdin=body))
        except GhError as err:
            if attempt < 3 and RATE_LIMIT_RE.search(str(err)):
                wait = 30 * (attempt + 1)
                log(f"  GitHub rate limit, waiting {wait} s")
                time.sleep(wait)
                continue
            raise
        except json.JSONDecodeError as err:
            raise GhError(f"unexpected answer from GitHub: {err}") from err
        if data.get("errors"):
            message = "; ".join(e.get("message", "?") for e in data["errors"])
            if attempt < 3 and RATE_LIMIT_RE.search(message):
                time.sleep(30 * (attempt + 1))
                continue
            raise GhError(message)
        return data.get("data") or {}
    raise GhError("GitHub rate limit did not clear after several retries")


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- data


@dataclass
class PullRequest:
    number: int
    title: str
    url: str
    author: str
    last_commit: datetime | None
    draft: bool

    @classmethod
    def from_node(cls, node: dict) -> "PullRequest | None":
        if not node or "number" not in node:
            return None
        commits = ((node.get("commits") or {}).get("nodes") or [])
        date = commits[0]["commit"]["committedDate"] if commits else None
        return cls(
            number=node["number"],
            title=node.get("title", ""),
            url=node.get("url", ""),
            author=((node.get("author") or {}).get("login") or "ghost"),
            last_commit=parse_date(date),
            draft=bool(node.get("isDraft")),
        )

    def recent(self, cutoff: datetime) -> bool:
        return self.last_commit is not None and self.last_commit >= cutoff


@dataclass
class Gap:
    rank: int
    gap_id: str
    blocker: bool
    title: str
    text: str
    hours: str
    doc: str
    trackers: list[str]
    terms: list[str] = field(default_factory=list)
    prs: list[PullRequest] = field(default_factory=list)


@dataclass
class Issue:
    number: int
    title: str
    url: str
    created: datetime
    labels: list[str]
    priority: int
    thumbs_up: int
    fixing_prs: list[PullRequest]
    mentioning_prs: list[PullRequest]
    skip_reason: str = ""


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------- gaps


def load_gaps_markdown(repo: str, ref: str, gaps_file: Path | None) -> str:
    if gaps_file:
        return gaps_file.read_text(encoding="utf-8")
    return gh(["api", "-H", "Accept: application/vnd.github.raw", f"repos/{repo}/contents/docs/gaps.md?ref={ref}"])


def strip_markdown(text: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return text.replace("**", "").replace("`", "").strip()


def doc_links(cell: str, repo: str, ref: str) -> str:
    def to_url(match: re.Match) -> str:
        name, target = match.group(1), match.group(2)
        if target.startswith("http"):
            return f"[{name}]({target})"
        path, _, anchor = target.partition("#")
        full = posixpath.normpath(posixpath.join("docs", path)) if path else "docs/gaps.md"
        url = f"https://github.com/{repo}/blob/{ref}/{full}" + (f"#{anchor}" if anchor else "")
        return f"[{name}]({url})"

    return re.sub(r"\[([^\]]*)\]\(([^)]*)\)", to_url, cell)


def parse_gaps(markdown: str, repo: str, ref: str) -> list[Gap]:
    gaps: list[Gap] = []
    in_ranked = False
    for line in markdown.splitlines():
        if line.startswith("## "):
            in_ranked = line.strip().lower() == "## ranked"
            continue
        if not in_ranked or not line.startswith("|"):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        match = re.match(r"^(\d+)\s*(B)?$", cells[0]) if cells else None
        if not match or len(cells) < 2:
            continue  # header or separator row
        gap_cell = cells[1]
        if gap_cell.startswith("~~"):
            continue  # closed gap, kept in the table for reference
        bold = re.search(r"\*\*(.+?)\*\*", gap_cell)
        title = strip_markdown(bold.group(1) if bold else gap_cell[:100])
        evidence = cells[2] if len(cells) > 2 else ""
        gaps.append(Gap(
            rank=len(gaps) + 1,
            gap_id=match.group(1),
            blocker=bool(match.group(2)),
            title=title.rstrip(" :"),
            text=strip_markdown(gap_cell),
            hours=cells[4] if len(cells) > 4 else "",
            doc=doc_links(cells[5], repo, ref) if len(cells) > 5 else "",
            trackers=list(dict.fromkeys(TRACKER_RE.findall(gap_cell + " " + evidence))),
        ))
    return gaps


def terms_for(gap: Gap, term_map: dict[str, list[str]]) -> list[str]:
    terms = [t for t in gap.trackers if t not in BROAD_TRACKERS]
    title = gap.title.lower().replace("’", "'")
    for key, words in term_map.items():
        if key.lower() in title:
            terms.extend(words)
    if not terms:  # nothing known: fall back on the first words of the title
        words = re.findall(r"[A-Za-z0-9][\w-]*", gap.title)
        terms.append(" ".join(words[:3]))
    return list(dict.fromkeys(terms))


def title_matches(title: str, term: str) -> bool:
    """Every word of the term starts a word of the title (GitHub's own phrase match is loose)."""
    lowered = title.lower()
    return all(re.search(r"(?<![a-z0-9])" + re.escape(word), lowered) for word in term.lower().split())


def search_prs(repo: str, term: str, cache: dict[str, list[PullRequest]]) -> list[PullRequest]:
    if term not in cache:
        query = f'repo:{repo} is:pr is:open "{term}" in:title'
        data = graphql(SEARCH_QUERY, {"q": query})
        nodes = (data.get("search") or {}).get("nodes") or []
        prs = (PullRequest.from_node(n) for n in nodes)
        cache[term] = [pr for pr in prs if pr and title_matches(pr.title, term)]
        time.sleep(0.4)  # stay clear of GitHub's secondary rate limit on search
    return cache[term]


def assess_gaps(gaps: list[Gap], repo: str, cutoff: datetime, term_map: dict[str, list[str]],
                top: int) -> tuple[list[Gap], list[Gap]]:
    cache: dict[str, list[PullRequest]] = {}
    free: list[Gap] = []
    busy: list[Gap] = []
    for gap in gaps:
        if len(free) >= top:
            break
        gap.terms = terms_for(gap, term_map)
        log(f"gap {gap.gap_id}: {gap.title[:60]}  ({len(gap.terms)} search terms)")
        found: dict[int, PullRequest] = {}
        for term in gap.terms:
            for pr in search_prs(repo, term, cache):
                found[pr.number] = pr
        gap.prs = sorted(found.values(), key=lambda p: p.last_commit or datetime.min.replace(tzinfo=timezone.utc),
                         reverse=True)
        (busy if any(pr.recent(cutoff) for pr in gap.prs) else free).append(gap)
    return free, busy


# ---------------------------------------------------------------- fixes


def live_claim(comments: list[dict], cutoff: datetime) -> str:
    """The login of someone whose claim in the window was not released after it, else ""."""
    claimant, claimed_at = "", None
    for comment in sorted(comments, key=lambda c: c.get("createdAt") or ""):
        body = (comment.get("body") or "").replace("’", "'")
        login = ((comment.get("author") or {}).get("login") or "")
        when = parse_date(comment.get("createdAt"))
        if RELEASE_RE.search(body):
            claimant, claimed_at = "", None
        elif CLAIM_RE.search(body) and not login.endswith("[bot]"):
            claimant, claimed_at = login, when
    return claimant if claimed_at and claimed_at >= cutoff else ""


def same_repo_open(pr_node: dict, repo: str) -> bool:
    owner_name = ((pr_node.get("repository") or {}).get("nameWithOwner") or "")
    return pr_node.get("state") == "OPEN" and owner_name.lower() == repo.lower()


def issue_from_node(node: dict, repo: str, priority: int, cutoff: datetime, include_assigned: bool,
                    excluded_labels: set[str], fix_labels: set[str]) -> Issue:
    labels = [l["name"] for l in (node.get("labels") or {}).get("nodes") or []]
    fixing: dict[int, PullRequest] = {}
    mentioning: dict[int, PullRequest] = {}
    for pr_node in (node.get("closedByPullRequestsReferences") or {}).get("nodes") or []:
        pr = PullRequest.from_node(pr_node)
        if pr and same_repo_open(pr_node, repo):
            fixing[pr.number] = pr
    for event in (node.get("timelineItems") or {}).get("nodes") or []:
        source = (event or {}).get("source") or {}
        pr = PullRequest.from_node(source)
        if not pr or not same_repo_open(source, repo):  # PRs of sibling apps mention issues here too
            continue
        (fixing if event.get("willCloseTarget") else mentioning)[pr.number] = pr
    for number in fixing:
        mentioning.pop(number, None)

    issue = Issue(
        number=node["number"],
        title=node.get("title", ""),
        url=node.get("url", ""),
        created=parse_date(node.get("createdAt")) or datetime.now(timezone.utc),
        labels=labels,
        priority=priority,
        thumbs_up=((node.get("reactions") or {}).get("totalCount") or 0),
        fixing_prs=sorted(fixing.values(), key=lambda p: p.number),
        mentioning_prs=sorted(mentioning.values(), key=lambda p: p.number),
    )
    lowered = {l.lower() for l in labels}
    assignees = [a["login"] for a in (node.get("assignees") or {}).get("nodes") or []]
    claimant = live_claim((node.get("comments") or {}).get("nodes") or [], cutoff)
    # The text before ":" is the category the report counts.
    if lowered & excluded_labels:
        issue.skip_reason = "labelled " + ", ".join(sorted(lowered & excluded_labels))
    elif fix_labels and not lowered & fix_labels:
        issue.skip_reason = "not a bug-type issue"
    elif assignees and not include_assigned:
        issue.skip_reason = "assigned: " + ", ".join(assignees)
    elif any(pr.recent(cutoff) for pr in issue.fixing_prs):
        issue.skip_reason = "fix in progress: " + ", ".join(f"#{p.number}" for p in issue.fixing_prs if p.recent(cutoff))
    elif claimant:
        issue.skip_reason = f"claimed in a comment: {claimant}"
    return issue


def assess_issues(repo: str, cutoff: datetime, top: int, include_assigned: bool, excluded_labels: set[str],
                  fix_labels: set[str], max_pages: int) -> tuple[list[Issue], list[Issue]]:
    owner, name = repo.split("/", 1)
    free: list[Issue] = []
    skipped: list[Issue] = []
    seen: set[int] = set()
    for priority, label in enumerate(PRIORITY_LABELS):
        if len(free) >= top:
            break
        log(f"issues labelled '{label}'")
        after = None
        for _ in range(max_pages):
            data = graphql(ISSUES_QUERY, {"owner": owner, "name": name, "label": label, "after": after})
            page = ((data.get("repository") or {}).get("issues") or {})
            for node in page.get("nodes") or []:
                if not node or node["number"] in seen:
                    continue
                seen.add(node["number"])
                issue = issue_from_node(node, repo, priority, cutoff, include_assigned, excluded_labels, fix_labels)
                (skipped if issue.skip_reason else free).append(issue)
            info = page.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            after = info.get("endCursor")
    free.sort(key=lambda i: (i.priority, -i.thumbs_up, i.created))
    return free[:top], skipped


# ---------------------------------------------------------------- report


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def pr_ref(pr: PullRequest, me: str) -> str:
    date = pr.last_commit.strftime("%Y-%m-%d") if pr.last_commit else "no commits"
    mine = " (yours)" if pr.author.lower() == me.lower() else ""
    draft = " draft" if pr.draft else ""
    return f"[#{pr.number}]({pr.url}) {pr.author}{mine}, {date}{draft}"


def render(args: argparse.Namespace, gaps_free: list[Gap], gaps_busy: list[Gap], issues: list[Issue],
           issues_skipped: list[Issue], cutoff: datetime, me: str, total_gaps: int) -> str:
    now = datetime.now(timezone.utc)
    out: list[str] = []
    add = out.append
    add(f"# LightCraft: next gaps and fixes\n")
    add(f"Generated {now:%Y-%m-%d %H:%M} UTC by `gap_report.py` from "
        f"[{args.repo}](https://github.com/{args.repo}) (`docs/gaps.md` at `{args.ref}`).\n")
    add(f"**Rule:** an item counts as taken when an open pull request on it had a commit in the last "
        f"{args.stale_days} days (since {cutoff:%Y-%m-%d}). Items whose pull requests are all older are kept, "
        f"with those pull requests listed.\n")

    add(f"## Top {args.top} gaps to work on ({len(gaps_free)} found)\n")
    add(f"From the {total_gaps} open gaps, in the order of `docs/gaps.md` (ranked by user impact, then effort). "
        f"**B** = blocks beta.")
    if len(gaps_free) < args.top:
        add(f"Only {len(gaps_free)} gaps have no pull request with a commit in the window; every other gap is "
            f"listed under *Skipped* below.")
    add("")
    if gaps_free:
        add("| # | Gap | B | Hours | Doc | Open PRs (all quiet) |")
        add("|---:|---|:-:|---|---|---|")
        for gap in gaps_free:
            prs = "<br>".join(pr_ref(p, me) for p in gap.prs) or "none"
            add(f"| {gap.gap_id} | **{cell(gap.title)}** | {'B' if gap.blocker else ''} | {cell(gap.hours)} | "
                f"{cell(gap.doc)} | {prs} |")
        add("")
        add("<details><summary>Full gap text and search terms</summary>\n")
        for gap in gaps_free:
            add(f"**Gap {gap.gap_id}: {gap.title}**  ")
            add(f"Trackers: {', '.join(gap.trackers) or 'none'} · searched PR titles for: "
                f"{', '.join(f'`{t}`' for t in gap.terms)}\n")
            add(f"> {cell(gap.text)}\n")
        add("</details>\n")
    else:
        add("_No gap without recent activity found._\n")

    add(f"## Top {args.top} fixes to apply ({len(issues)} found)\n")
    add("Open issues with a priority label and a bug, regression, security or performance label; not assigned, "
        "not labelled needs info / in progress, no recent fixing pull request and no live claim in a comment. "
        "Sorted by priority, then 👍, then oldest first.\n")
    if issues:
        add("| Issue | Priority | Labels | Opened | 👍 | Quiet fixing PRs | Mentioned in (open PRs) |")
        add("|---|---|---|---|---:|---|---|")
        for issue in issues:
            priority = PRIORITY_LABELS[issue.priority].split(": ", 1)[-1]
            labels = ", ".join(l for l in issue.labels if not l.startswith("priority"))
            fixing = "<br>".join(pr_ref(p, me) for p in issue.fixing_prs) or "none"
            mentioned = "<br>".join(pr_ref(p, me) for p in issue.mentioning_prs[:4]) or ""
            if len(issue.mentioning_prs) > 4:
                mentioned += f"<br>+{len(issue.mentioning_prs) - 4} more"
            add(f"| [#{issue.number}]({issue.url}) {cell(issue.title)} | {priority} | {cell(labels)} | "
                f"{issue.created:%Y-%m-%d} | {issue.thumbs_up} | {fixing} | {mentioned} |")
        add("")
    else:
        add("_No issue matched._\n")

    add("## Skipped because of recent activity\n")
    add("### Gaps\n")
    if gaps_busy:
        for gap in gaps_busy:
            recent = [p for p in gap.prs if p.recent(cutoff)]
            shown = "; ".join(pr_ref(p, me) for p in recent[:3])
            more = f" (+{len(recent) - 3} more)" if len(recent) > 3 else ""
            add(f"- **{gap.gap_id}{' B' if gap.blocker else ''} {cell(gap.title)}**: {len(recent)} active: {shown}{more}")
        add("")
    else:
        add("_None._\n")
    add("### Issues\n")
    counts: dict[str, int] = {}
    for issue in issues_skipped:
        key = issue.skip_reason.split(":", 1)[0]
        counts[key] = counts.get(key, 0) + 1
    if counts:
        for key, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            add(f"- {key}: {count}")
        add("")
    else:
        add("_None._\n")

    add("## Before you start\n")
    add("- House rule 2: read the issue comments, search pull requests for `#N` and the keywords, check recent "
        "commits on `main`, then **comment on the issue to claim it**. Release a claim you will not finish.")
    add("- Gaps are matched to pull requests by keywords in PR titles (see the search terms above), so a PR with "
        "an unusual title can be missed, and a broad keyword can match an unrelated PR. Check before claiming.")
    add("- \"Mentioned in\" lists open PRs that reference an issue without closing it; they may or may not fix it.")
    add("- Update the gap's row in `docs/gaps.md` and `docs/parity-checklist.md` in the same commit when you land a feature.")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- main


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"owner/name (default {DEFAULT_REPO})")
    parser.add_argument("--ref", default="main", help="branch of docs/gaps.md (default main)")
    parser.add_argument("--gaps-file", type=Path, help="read a local docs/gaps.md instead of GitHub's")
    parser.add_argument("--top", type=int, default=10, help="how many gaps and fixes to list (default 10)")
    parser.add_argument("--stale-days", type=int, default=28,
                        help="a PR with no commit for this many days is quiet (default 28 = 4 weeks)")
    parser.add_argument("--terms-file", type=Path, help="JSON {title piece: [PR title terms]} added to the defaults")
    parser.add_argument("--include-assigned", action="store_true", help="keep assigned issues")
    parser.add_argument("--include-needs-info", action="store_true", help="keep issues labelled 'needs info'")
    parser.add_argument("--any-issue-type", action="store_true", help="keep issues without a bug-type label")
    parser.add_argument("--max-pages", type=int, default=10, help="pages of 30 issues per priority label (default 10)")
    parser.add_argument("-o", "--output", type=Path,
                        help="Markdown file to write (default reports/lightcraft-next-<date>.md next to this script)")
    args = parser.parse_args()

    if args.top < 1 or args.stale_days < 0 or "/" not in args.repo:
        parser.error("--top must be ≥ 1, --stale-days ≥ 0 and --repo owner/name")

    term_map = dict(DEFAULT_TERMS)
    if args.terms_file:
        try:
            extra = json.loads(args.terms_file.read_text(encoding="utf-8"))
            term_map.update({str(k): [str(t) for t in v] for k, v in extra.items()})
        except (OSError, ValueError, AttributeError, TypeError) as err:
            parser.error(f"--terms-file: {err}")

    excluded = set(EXCLUDED_LABELS)
    if args.include_needs_info:
        excluded.discard("needs info")
    fix_labels = set() if args.any_issue_type else set(FIX_LABELS)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.stale_days)
    output = args.output or Path(__file__).resolve().parent / "reports" / f"lightcraft-next-{datetime.now():%Y-%m-%d}.md"

    try:
        me = json.loads(gh(["api", "user"])).get("login", "")
        gaps = parse_gaps(load_gaps_markdown(args.repo, args.ref, args.gaps_file), args.repo, args.ref)
        if not gaps:
            raise GhError("no gaps found: docs/gaps.md has no '## Ranked' table in the expected shape")
        gaps_free, gaps_busy = assess_gaps(gaps, args.repo, cutoff, term_map, args.top)
        issues, issues_skipped = assess_issues(args.repo, cutoff, args.top, args.include_assigned, excluded,
                                               fix_labels, args.max_pages)
    except (GhError, OSError) as err:
        log(f"gap_report: {err}")
        return 1

    report = render(args, gaps_free, gaps_busy, issues, issues_skipped, cutoff, me, len(gaps))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    log(f"\nWrote {output}: {len(gaps_free)} gaps, {len(issues)} fixes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
