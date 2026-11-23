#!/usr/bin/env python3
"""Check that the repository carries no development-tool files and no tool attribution in its commits.

It fails (exit 1) when:
- the tree tracks a development-tool file: CLAUDE.md, AGENTS.md, .claude/, .cursor*, .aider*, .windsurf*,
  .github/copilot-instructions.md;
- a commit in the range has an author or committer email of noreply@anthropic.com, or a message containing a
  tool attribution trailer (Co-Authored-By: Claude, Claude-Session, "Generated with [Claude",
  noreply@anthropic.com).

It does not look at file contents, so product code that names an AI service (for example a vision provider or
an MCP connector) is fine.

Usage:
    python tools/check_provenance.py                   # tracked files and every commit
    python tools/check_provenance.py --range A..B      # tracked files and the commits in A..B
    python tools/check_provenance.py --no-commits      # tracked files only
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

BAD_NAMES = {"claude.md", "agents.md"}
BAD_PREFIXES = (".claude", ".cursor", ".aider", ".windsurf")
BAD_PATHS = {".github/copilot-instructions.md"}

BAD_EMAILS = {"noreply@anthropic.com"}
BAD_MESSAGE = [
    re.compile(r"co-authored-by:[^\n]*claude", re.I),
    re.compile(r"claude-session", re.I),
    re.compile(r"generated with \[claude", re.I),
    re.compile(r"noreply@anthropic\.com", re.I),
]


def bad_path(path: str) -> str | None:
    """Why this tracked path is a development-tool file, or None."""
    p = PurePosixPath(path.replace("\\", "/"))
    if str(p).lower() in BAD_PATHS:
        return "tool instructions file"
    for part in p.parts:
        low = part.lower()
        if low.startswith(BAD_PREFIXES):
            return f"tool folder or file {part}"
    if p.name.lower() in BAD_NAMES:
        return f"tool instructions file {p.name}"
    return None


def bad_commit(author_email: str, committer_email: str, message: str) -> list[str]:
    """Reasons this commit carries tool attribution; empty when clean."""
    out = []
    if author_email.strip().lower() in BAD_EMAILS:
        out.append(f"author email {author_email.strip()}")
    if committer_email.strip().lower() in BAD_EMAILS:
        out.append(f"committer email {committer_email.strip()}")
    for rx in BAD_MESSAGE:
        m = rx.search(message)
        if m:
            out.append(f"message contains {m.group(0)!r}")
    return out


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def tracked_files(root: Path) -> list[str]:
    try:
        return [x for x in _git(root, "ls-files", "-z").split("\0") if x]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*")
                if ".git" not in p.relative_to(root).parts]


def commits(root: Path, rev_range: str | None) -> list[tuple[str, str, str, str]]:
    """(sha, author email, committer email, message) for each commit in the range (default: all)."""
    sep, end = "\x1f", "\x1e"
    args = ["log", f"--format=%H{sep}%ae{sep}%ce{sep}%B{end}"]
    args.append(rev_range if rev_range else "--all")
    out = _git(root, *args)
    rows = []
    for rec in out.split(end):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, ae, ce, msg = rec.split(sep, 3)
        rows.append((sha, ae, ce, msg))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="repository root (default: current folder)")
    ap.add_argument("--range", dest="rev_range", help="commit range such as BASE..HEAD (default: all commits)")
    ap.add_argument("--no-commits", action="store_true", help="check tracked files only")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    problems = []
    for f in tracked_files(root):
        why = bad_path(f)
        if why:
            problems.append(f"file {f}: {why}")
    if not a.no_commits:
        try:
            for sha, ae, ce, msg in commits(root, a.rev_range):
                for why in bad_commit(ae, ce, msg):
                    problems.append(f"commit {sha[:12]}: {why}")
        except subprocess.CalledProcessError as e:
            print(f"could not read commits: {e.stderr.strip()}", file=sys.stderr)
            return 2
    if problems:
        print("Provenance check failed:")
        for p in problems:
            print(f"  {p}")
        return 1
    print("Provenance check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
