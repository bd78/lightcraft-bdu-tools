#!/usr/bin/env python3
"""Look for credentials in every uncommitted change before it leaves the machine.

Scans what `coderabbit review --uncommitted --include-untracked` would upload: the lines added in
staged changes (`git diff --cached`), in unstaged changes (`git diff`) and the content of new,
non-ignored files. Prints only `where: file:line (kind)`, never the matched value, and exits 1 when
something is found (0 when clean, 2 on error).

Usage:  python check_secrets.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

MAX_FILE_BYTES = 2_000_000

# Formats that are credentials on sight, and assignments of a long value to a credential-like name.
RULES: list[tuple[str, re.Pattern[str]]] = [
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("API key (sk-)", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
]

# A credential-like name being given a value: `api_key = …`, `"api_key": …` (JSON, YAML, PowerShell
# hashtables), with or without a prefix (AWS_SECRET_ACCESS_KEY, GITHUB_TOKEN, db_password). The value
# itself is judged by credential_value().
ASSIGNMENT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])[\w-]*?(?:api[_-]?key|secret|token|passw(?:or)?d|pwd|auth)[\w-]*[\"']?\s*[:=]\s*")
MIN_VALUE_LENGTH = 12
# A value that only names a variable: ${VAR}, $env:VAR, %VAR%, {{ var }}; unquoted, also $VAR. A quoted
# "$abc…" stays a candidate: in JSON or YAML it is a literal, and a false alarm is cheaper than a leak.
QUOTED_VARIABLE_RE = re.compile(r"^(?:\$\{[\w.:]+\}|\$env:\w+|%\w+%|\{\{\s*[\w.]+\s*\}\})$", re.IGNORECASE)
VARIABLE_RE = re.compile(r"^(?:\$\{?[\w.:]+\}?|%\w+%|\{\{\s*[\w.]+\s*\}\})$")
CODE_CHARS = set("()[]{}<>\"'$")


def credential_value(rest: str) -> bool:
    """Whether the text after `name =` / `name:` starts with a literal that looks like a credential."""
    if rest[:1] in ("'", '"'):
        end = rest.find(rest[0], 1)
        if end < 0:
            return False  # no closing quote: a multi-line or broken value, not judged here
        value = rest[1:end]
        return len(value) >= MIN_VALUE_LENGTH and not QUOTED_VARIABLE_RE.match(value)
    match = re.match(r"[^\s,;#]+", rest)
    value = match.group(0) if match else ""
    if len(value) < MIN_VALUE_LENGTH or VARIABLE_RE.match(value):
        return False
    return not CODE_CHARS & set(value)  # a call, index, string or variable: code, not a literal

BOMS: list[tuple[bytes, str]] = [
    (b"\xff\xfe\x00\x00", "utf-32-le"), (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"),
]


def git_bytes(*args: str) -> bytes:
    proc = subprocess.run(["git", *args], capture_output=True, check=False)
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or f"git {' '.join(args)} failed")
    return proc.stdout


def git(*args: str) -> str:
    return git_bytes(*args).decode("utf-8", errors="replace")


def scan_line(text: str) -> list[str]:
    kinds = [name for name, pattern in RULES if pattern.search(text)]
    if any(credential_value(text[m.end():]) for m in ASSIGNMENT_RE.finditer(text)):
        kinds.append("credential assignment")
    return kinds


def decode(data: bytes) -> str | None:
    """The text of a file in a BOM-marked or BOM-less UTF-8/16/32 encoding, or None if it isn't text."""
    for bom, encoding in BOMS:
        if data.startswith(bom):
            return data.decode(encoding, errors="replace")
    head = data[:8192]
    if b"\0" not in head:
        return data.decode("utf-8", errors="replace")
    # BOM-less UTF-16: ASCII text puts a NUL in every other byte.
    even_nuls, odd_nuls = head[0::2].count(0), head[1::2].count(0)
    half = max(len(head) // 2, 1)
    if odd_nuls / half > 0.3 and even_nuls / half < 0.05:
        return data.decode("utf-16-le", errors="replace")
    if even_nuls / half > 0.3 and odd_nuls / half < 0.05:
        return data.decode("utf-16-be", errors="replace")
    return None


def scan_bytes(data: bytes, name: str, where: str) -> list[str]:
    if len(data) > MAX_FILE_BYTES:
        return [f"{where}: {name}:0 (too large to scan, check it by hand)"]
    text = decode(data)
    if text is None:
        return [f"{where}: {name}:0 (binary, not scanned: check it by hand or keep it out of the change)"]
    hits: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for kind in scan_line(line):
            hits.append(f"{where}: {name}:{number} ({kind})")
    return hits


def scan_diff(diff: str, where: str) -> list[str]:
    """Scan the added lines of a diff; files git shows as binary (UTF-16 text too) are scanned whole."""
    hits: list[str] = []
    path, line_no = "", 0
    old_left = new_left = 0  # lines still to come in the current hunk
    for line in diff.splitlines():
        if old_left > 0 or new_left > 0:
            # Inside a hunk every line is content, even one that looks like a header ("+++ …").
            if line.startswith("+"):
                for kind in scan_line(line[1:]):
                    hits.append(f"{where}: {path}:{line_no} ({kind})")
                line_no += 1
                new_left -= 1
            elif line.startswith("-"):
                old_left -= 1
            elif line.startswith(" "):
                line_no += 1
                old_left -= 1
                new_left -= 1
            continue  # "\ No newline at end of file" and anything else
        hunk = re.match(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if hunk:
            old_left = int(hunk.group(1)) if hunk.group(1) is not None else 1
            line_no = int(hunk.group(2))
            new_left = int(hunk.group(3)) if hunk.group(3) is not None else 1
            continue
        binary = re.match(r"^Binary files .* and (?:b/)?(.+) differ$", line)
        if binary:
            name = binary.group(1)
            if name == "/dev/null":
                continue  # a deleted file uploads nothing new
            try:
                data = git_bytes("show", f":{name}") if where == "staged" else Path(name).read_bytes()
            except (RuntimeError, OSError):
                hits.append(f"{where}: {name}:0 (unreadable, check it by hand)")
                continue
            hits.extend(scan_bytes(data, name, where))
        elif line.startswith("+++ "):  # file header: only ever seen outside a hunk
            path = line[6:] if line.startswith("+++ b/") else line[4:]
    return hits


def scan_untracked() -> list[str]:
    hits: list[str] = []
    for name in filter(None, git("ls-files", "--others", "--exclude-standard", "-z").split("\0")):
        path = Path(name)
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                hits.append(f"new file: {name}:0 (too large to scan, check it by hand)")
                continue
            data = path.read_bytes()
        except OSError as err:
            hits.append(f"new file: {name}:0 (unreadable: {err.strerror})")
            continue
        hits.extend(scan_bytes(data, name, "new file"))
    return hits


def main() -> int:
    try:
        root = git("rev-parse", "--show-toplevel").strip()
        if root:
            os.chdir(root)
        hits = scan_diff(git("diff", "--cached", "-U0", "--no-color", "--no-ext-diff"), "staged")
        hits += scan_diff(git("diff", "-U0", "--no-color", "--no-ext-diff"), "unstaged")
        hits += scan_untracked()
    except (RuntimeError, OSError) as err:
        print(f"check_secrets: {err}", file=sys.stderr)
        return 2
    if not hits:
        print("check_secrets: no credentials found in uncommitted changes.")
        return 0
    print("check_secrets: possible credentials (values not shown):")
    for hit in hits:
        print(f"  {hit}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
