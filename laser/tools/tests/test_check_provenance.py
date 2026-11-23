"""Tests for tools/check_provenance.py. Run with: python -m pytest -q tools/tests"""
import importlib.util
import subprocess
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location("check_provenance", Path(__file__).resolve().parents[1] / "check_provenance.py")
cp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cp)


@pytest.mark.parametrize("path", ["CLAUDE.md", "docs/claude.md", "AGENTS.md", ".claude/settings.json",
                                  ".cursorrules", ".cursor/rules/x.mdc", ".aider.conf.yml",
                                  ".github/copilot-instructions.md", ".windsurfrules"])
def test_tool_files_are_flagged(path):
    assert cp.bad_path(path)


@pytest.mark.parametrize("path", ["backend/app/routers/mcp.py", "backend/app/services/vision.py",
                                  "backend/tests/test_oauth.py", "README.md", "docs/claude-connector.png",
                                  ".github/workflows/ci.yml", "frontend/src/lib/cursor.js"])
def test_product_files_pass(path):
    assert cp.bad_path(path) is None


def test_commit_attribution_is_flagged():
    assert cp.bad_commit("noreply@anthropic.com", "a@example.com", "x")
    assert cp.bad_commit("a@example.com", "noreply@anthropic.com", "x")
    assert cp.bad_commit("a@example.com", "a@example.com", "fix\n\nCo-Authored-By: Claude <x@example.com>\n")
    assert cp.bad_commit("a@example.com", "a@example.com", "fix\n\nClaude-Session: https://example.com/s\n")
    assert cp.bad_commit("a@example.com", "a@example.com", "Generated with [Claude Code](https://example.com)")
    assert cp.bad_commit("a@example.com", "a@example.com", "thanks noreply@anthropic.com")


def test_product_words_in_messages_pass():
    assert cp.bad_commit("a@example.com", "a@example.com", "Add the Claude vision provider and MCP connector") == []
    assert cp.bad_commit("a@example.com", "a@example.com", "Co-Authored-By: Jane Doe <jane@example.com>") == []


def _git(repo, *args, env=None):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _commit(repo, name, msg, email="dev@example.com"):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text("x\n")
    _git(repo, "add", name)
    _git(repo, "-c", "user.name=Dev", "-c", f"user.email={email}", "commit", "-q", "-m", msg)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    return tmp_path


def test_clean_repo_passes(repo, capsys):
    _commit(repo, "app/mcp.py", "Add the MCP endpoint so Claude can connect")
    assert cp.main(["--root", str(repo)]) == 0


def test_tracked_tool_file_fails(repo):
    _commit(repo, "CLAUDE.md", "notes")
    assert cp.main(["--root", str(repo), "--no-commits"]) == 1


def test_bad_commit_fails_and_range_limits_the_check(repo):
    first = _commit(repo, "a.txt", "start\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    _commit(repo, "b.txt", "clean change")
    assert cp.main(["--root", str(repo)]) == 1
    assert cp.main(["--root", str(repo), "--range", f"{first}..HEAD"]) == 0


def test_bad_author_email_fails(repo):
    _commit(repo, "a.txt", "clean message", email="noreply@anthropic.com")
    assert cp.main(["--root", str(repo)]) == 1
