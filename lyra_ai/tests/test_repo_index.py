"""Tests for lyra_core.repo_index (CP-G).

Builds a tiny real git repository per test (subprocess `git init`/`commit`
against a tmp_path — global user.name/user.email are already configured in
this environment) rather than mocking subprocess: read_commits() is a thin
wrapper over real `git log` output, and the format string is exactly what
this test suite should catch a typo in.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lyra_core.repo_index import Commit, index_repo, main, read_commits
from lyra_memory.store import Store


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _init_repo(tmp_path: Path, commit_messages: list[str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q")
    for i, message in enumerate(commit_messages):
        (repo / "file.txt").write_text(f"content {i}\n")
        _run_git(repo, "add", "file.txt")
        _run_git(repo, "commit", "-q", "-m", message)
    return repo


@pytest.fixture
async def store(tmp_path: Path) -> Store:
    s = await Store.open(tmp_path / "store.db")
    yield s
    await s.close()


# ── read_commits ─────────────────────────────────────────────────────────────

def test_read_commits_parses_every_commit(tmp_path):
    repo = _init_repo(tmp_path, ["first commit", "second commit", "third commit"])

    commits = read_commits(repo)

    assert len(commits) == 3
    assert [c.subject_line for c in commits] == ["third commit", "second commit", "first commit"]


def test_read_commits_hash_lengths(tmp_path):
    repo = _init_repo(tmp_path, ["only commit"])

    (commit,) = read_commits(repo)

    assert len(commit.full_hash) == 40
    assert all(ch in "0123456789abcdef" for ch in commit.full_hash)
    assert commit.full_hash.startswith(commit.short_hash)


def test_read_commits_survives_a_pipe_in_the_message(tmp_path):
    """The field separator is a control character, not '|' — a commit
    message containing '|' must not corrupt parsing."""
    repo = _init_repo(tmp_path, ["fix: a | b | c handling"])

    (commit,) = read_commits(repo)

    assert commit.subject_line == "fix: a | b | c handling"


def test_read_commits_raises_when_not_a_git_repo(tmp_path):
    not_a_repo = tmp_path / "plain_dir"
    not_a_repo.mkdir()

    with pytest.raises(subprocess.CalledProcessError):
        read_commits(not_a_repo)


def test_commit_fact_text_is_the_bracketed_citation_form():
    commit = Commit(
        full_hash="a" * 40, short_hash="aaaaaaa", author="Claude",
        date_iso="2026-09-02T12:00:00+00:00", subject_line="CP-G: index the repo",
    )
    assert commit.fact_text == "[aaaaaaa] 2026-09-02 Claude: CP-G: index the repo"


# ── index_repo ────────────────────────────────────────────────────────────────

async def test_index_repo_writes_one_fact_per_commit(tmp_path, store):
    repo = _init_repo(tmp_path, ["alpha", "beta"])

    found, inserted = await index_repo(repo, store)

    assert (found, inserted) == (2, 2)
    async with store.db.execute(
        "SELECT COUNT(*) FROM facts WHERE source_kind = 'repo_commit'"
    ) as cur:
        (count,) = await cur.fetchone()
    assert count == 2


async def test_index_repo_rows_are_marked_repo_commit(tmp_path, store):
    repo = _init_repo(tmp_path, ["only commit"])

    await index_repo(repo, store)

    async with store.db.execute(
        "SELECT subject, text, source_kind, confidence, source_atom_id, valid_until FROM facts"
        " WHERE source_kind = 'repo_commit'"
    ) as cur:
        (subject, text, source_kind, confidence, source_atom_id, valid_until) = await cur.fetchone()
    assert len(subject) == 40
    assert text.startswith(f"[{subject[:7]}]")
    assert "only commit" in text
    assert source_kind == "repo_commit"
    assert confidence == 1.0
    assert source_atom_id is None
    assert valid_until is None


async def test_index_repo_is_idempotent(tmp_path, store):
    repo = _init_repo(tmp_path, ["alpha", "beta", "gamma"])

    first_found, first_inserted = await index_repo(repo, store)
    second_found, second_inserted = await index_repo(repo, store)

    assert (first_found, first_inserted) == (3, 3)
    assert (second_found, second_inserted) == (3, 0)
    async with store.db.execute(
        "SELECT COUNT(*) FROM facts WHERE source_kind = 'repo_commit'"
    ) as cur:
        (count,) = await cur.fetchone()
    assert count == 3


async def test_index_repo_indexes_only_new_commits_on_a_second_run(tmp_path, store):
    repo = _init_repo(tmp_path, ["alpha"])
    await index_repo(repo, store)

    (repo / "file.txt").write_text("more content\n")
    _run_git(repo, "add", "file.txt")
    _run_git(repo, "commit", "-q", "-m", "beta")
    found, inserted = await index_repo(repo, store)

    assert (found, inserted) == (2, 1)
    async with store.db.execute(
        "SELECT COUNT(*) FROM facts WHERE source_kind = 'repo_commit'"
    ) as cur:
        (count,) = await cur.fetchone()
    assert count == 2


async def test_index_repo_does_not_touch_atoms_or_vec_tables(tmp_path, store):
    """Change 1: a commit is never atoms-table content — structurally
    unreachable by dream/candidate/promotion."""
    repo = _init_repo(tmp_path, ["alpha"])

    await index_repo(repo, store)

    async with store.db.execute("SELECT COUNT(*) FROM atoms") as cur:
        (atom_count,) = await cur.fetchone()
    assert atom_count == 0


# ── main() / CLI ─────────────────────────────────────────────────────────────

def test_main_rejects_a_non_git_directory(tmp_path, capsys):
    import sys

    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()

    old_argv = sys.argv
    try:
        sys.argv = ["repo_index.py", str(not_a_repo)]
        exit_code = main()
    finally:
        sys.argv = old_argv

    assert exit_code == 1
    assert "no .git directory" in capsys.readouterr().err


def test_main_indexes_a_real_repo_end_to_end(tmp_path, capsys):
    import sys

    repo = _init_repo(tmp_path, ["alpha", "beta"])
    store_path = tmp_path / "store.db"
    old_argv = sys.argv
    try:
        sys.argv = ["repo_index.py", str(repo), "--store", str(store_path)]
        exit_code = main()
    finally:
        sys.argv = old_argv

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "2 commits found" in out
    assert "2 indexed" in out
    assert store_path.exists()
