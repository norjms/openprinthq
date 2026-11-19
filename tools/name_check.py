#!/usr/bin/env python3
"""Checks that the product's old name is gone from this repository.

    python3 tools/name_check.py [--self-test]

The product is OpenPrintHQ. Its old name may stay only where renaming would break something that already
exists: names stored on installations, devices and stores. Those are listed, as regular expressions, in
`.name-check.json` beside the paths that are never checked (changelogs and other records of what happened).

`.name-check.json`:
  mode                 "report" prints how much is left and passes; "enforce" lists every line and fails.
  allow                names that stay for good.
  allow_until_renamed  names that go when the repositories and images are renamed; then this list is emptied.
  skip                 paths (shell patterns) that are not checked.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

OLD = re.compile(r"artisan[ _-]?hub", re.IGNORECASE)
CONFIG = ".name-check.json"


def load(root: Path) -> dict:
    data = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    if data.get("mode") not in ("report", "enforce"):
        raise SystemExit(f"{CONFIG}: mode must be report or enforce")
    return data


def left(text: str, allow: list[re.Pattern]) -> bool:
    """Whether the old name is still in text once the allowed names are taken out."""
    for pattern in allow:
        text = pattern.sub("", text)
    return bool(OLD.search(text))


def find(root: Path, config: dict, files: list[str]) -> list[tuple[str, int]]:
    allow = [re.compile(p) for p in config.get("allow", []) + config.get("allow_until_renamed", [])]
    skip = [CONFIG, *config.get("skip", [])]
    hits = []
    for name in files:
        if any(fnmatch.fnmatch(name, pattern) for pattern in skip):
            continue
        if left(name, allow):
            hits.append((name, 0))
        path = root / name
        if path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # not text
        hits.extend((name, n) for n, line in enumerate(text.splitlines(), 1) if left(line, allow))
    return hits


def tracked(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True).stdout
    return [name for name in out.decode("utf-8", "replace").split("\0") if name]


def self_test() -> None:
    allow = [re.compile(r"artisanhub-manager"), re.compile(r"artisan_hub_[a-z_]+")]
    old = "Artisan" + "Hub"
    assert left(f"Welcome to {old}", allow)
    assert left("the " + "artisan" + "-hub image", allow)
    assert left("Artisan" + " Hub", allow)
    assert not left("volume artisanhub-manager and option artisan_hub_api_key", allow)
    assert left(f"artisanhub-manager for {old}", allow)
    assert not left("Artisan Prints, the shop", allow)
    print("name-check: self-test passed")


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        self_test()
        return 0
    root = Path(__file__).resolve().parent.parent
    config = load(root)
    hits = find(root, config, tracked(root))
    files = sorted({name for name, _ in hits})
    if not hits:
        print("name-check: the old name is only where it must stay")
        return 0
    if config["mode"] == "report":
        print(f"name-check (report only): the old name is still on {len(hits)} lines in {len(files)} files")
        counts = {name: sum(1 for hit, _ in hits if hit == name) for name in files}
        for name in sorted(files, key=lambda f: -counts[f])[:15]:
            print(f"  {counts[name]:5d}  {name}")
        return 0
    for name, n in hits:
        print(f"{name}:{n}" if n else f"{name}: file name")
    print(f"name-check: the old name is on {len(hits)} lines in {len(files)} files; rename them, or list a "
          f"name that must stay in {CONFIG}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
