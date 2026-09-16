"""lyra_core.repo_index — CP-G: index one repository's commit history.

Reads `git log` from a repo path given on the command line and writes one
`facts` row per commit (source_kind="repo_commit" — see interface.py's
REPO_COMMIT_SOURCE_KIND comment for the full row-shape rationale: no schema
change, structurally unreachable by dream/candidate/promotion, and excluded
from ordinary conversational retrieval by _facts_block's exact-subject-match
design rather than by a new filter).

Invoked explicitly, not on the turn path (CHANGES item 2):

    lyra_ai/venv/bin/python3 -m lyra_core.repo_index /path/to/repo
    lyra_ai/venv/bin/python3 -m lyra_core.repo_index /path/to/repo --store /path/to/store.db

Idempotent: re-running against the same repo does not duplicate rows — every
commit's full hash becomes `facts.subject`, and any subject already present
with source_kind="repo_commit" is skipped before the INSERT, not relied on
after it (no UNIQUE constraint on `facts.subject` — a schema change is out of
scope, so idempotency is enforced here, in Python, exactly the way
`schema.py`'s own comment says a growing vocabulary already has to be).

Does NOT import lyra_core.runtime.Runtime, lyra_core.runtime.TurnHandler, or
lyra_core.transport — it has no business starting a daemon or a listener
(the same discipline lyra_core/report.py's module docstring states for
itself). Does not import or touch the embedding model — commit facts are
never embedded (retrieve_repo_context() in interface.py is a plain SQL scan,
not KNN), so this tool needs no LYRA_EMBED_BACKEND and no network.
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LYRA_AI = REPO_ROOT / "lyra_ai"
if str(LYRA_AI) not in sys.path:
    sys.path.insert(0, str(LYRA_AI))

from lyra_memory.store import Store  # noqa: E402

# CP-G change 1: the same value interface.py's REPO_COMMIT_SOURCE_KIND
# names — duplicated as a plain string rather than imported, so this tool
# never has to import lyra_core.interface (and everything interface.py
# pulls in transitively) just to index a repo. Both sides are recorded in
# DECISIONS.md; a mismatch between them would be caught immediately by any
# repo-query turn finding zero commits.
SOURCE_KIND = "repo_commit"

# Field separator between git-log fields and commits — control characters
# that cannot appear in a commit's author name or subject line (unlike a
# printable delimiter such as '|', which a commit message could legally
# contain).
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"
_LOG_FORMAT = f"%H{_FIELD_SEP}%h{_FIELD_SEP}%an{_FIELD_SEP}%aI{_FIELD_SEP}%s{_RECORD_SEP}"


class Commit:
    __slots__ = ("full_hash", "short_hash", "author", "date_iso", "subject_line")

    def __init__(self, full_hash: str, short_hash: str, author: str, date_iso: str, subject_line: str) -> None:
        self.full_hash = full_hash
        self.short_hash = short_hash
        self.author = author
        self.date_iso = date_iso
        self.subject_line = subject_line

    @property
    def valid_from(self) -> float:
        return datetime.fromisoformat(self.date_iso).timestamp()

    @property
    def fact_text(self) -> str:
        # Already in the exact bracketed-citation form the repo-context
        # block displays (interface.py's retrieve_repo_context) — indexing
        # does the formatting once, not on every retrieval.
        date_only = self.date_iso[:10]
        return f"[{self.short_hash}] {date_only} {self.author}: {self.subject_line}"


def read_commits(repo_path: Path) -> list[Commit]:
    """Run `git log` in `repo_path` and parse it into Commit objects.

    Raises subprocess.CalledProcessError if `repo_path` is not a git
    repository (or git is not on PATH) — a loud failure, not an empty
    index silently standing in for "nothing to index."
    """
    result = subprocess.run(
        ["git", "log", f"--format={_LOG_FORMAT}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    commits: list[Commit] = []
    for record in result.stdout.split(_RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        fields = record.split(_FIELD_SEP)
        if len(fields) != 5:
            continue
        full_hash, short_hash, author, date_iso, subject_line = fields
        commits.append(Commit(full_hash, short_hash, author, date_iso, subject_line))
    return commits


async def index_repo(repo_path: Path, store: Store) -> tuple[int, int]:
    """Write one `facts` row per not-yet-indexed commit. Returns
    (commits_found, commits_inserted) — the gap between them is what
    idempotency looks like on a second run against the same repo."""
    commits = read_commits(repo_path)

    async with store.db.execute(
        "SELECT subject FROM facts WHERE source_kind = ?", (SOURCE_KIND,)
    ) as cur:
        already_indexed = {row[0] for row in await cur.fetchall()}

    now = datetime.now(timezone.utc).timestamp()
    inserted = 0
    for commit in commits:
        if commit.full_hash in already_indexed:
            continue
        await store.db.execute(
            "INSERT INTO facts"
            " (ts, subject, text, source_atom_id, source_kind, confidence, valid_from, valid_until)"
            " VALUES (?, ?, ?, NULL, ?, ?, ?, NULL)",
            (now, commit.full_hash, commit.fact_text, SOURCE_KIND, 1.0, commit.valid_from),
        )
        inserted += 1
    await store.db.commit()
    return (len(commits), inserted)


async def _run(repo_path: Path, store_path: Path | None) -> int:
    store = await Store.open(store_path) if store_path is not None else await Store.open()
    try:
        found, inserted = await index_repo(repo_path, store)
    finally:
        await store.close()

    skipped = found - inserted
    print(
        f"repo_index: {repo_path} — {found} commits found, "
        f"{inserted} indexed, {skipped} already present (skipped)."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", type=Path, help="path to the git repository to index")
    parser.add_argument(
        "--store", type=Path, default=None,
        help="path to store.db (default: lyra_memory.config.STORE_PATH)",
    )
    args = parser.parse_args()

    if not (args.repo / ".git").exists():
        print(f"no .git directory found under {args.repo}", file=sys.stderr)
        return 1

    return asyncio.run(_run(args.repo, args.store))


if __name__ == "__main__":
    sys.exit(main())
