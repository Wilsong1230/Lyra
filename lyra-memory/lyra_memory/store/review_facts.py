"""Print extracted facts next to the atoms they came from, for a manual read.

Step 8's verification is "facts extracted are true — manual read, ~50 rows".
There is no automated version of that: whether "studies at FGCU" is true is
not a property of the code. What the code can do is put the claim and its
evidence side by side so the read is quick and honest.

    python -m lyra_memory.store.review_facts            # 50 rows
    python -m lyra_memory.store.review_facts --limit 200
    python -m lyra_memory.store.review_facts --conflicts # only flagged pairs

Read for three things:
  * Is the claim true?
  * Does the source atom actually support it, or was it inferred past the
    evidence?
  * Is `source_kind` right? A `document` claim reported as `stated` is the
    failure that matters most — it launders a file's authority.
"""
from __future__ import annotations

import argparse
import asyncio
import textwrap
from datetime import datetime
from pathlib import Path

from lyra_memory.store import Store


def _wrap(text: str, indent: str = "      ") -> str:
    return textwrap.fill(text, width=88, initial_indent=indent,
                         subsequent_indent=indent)


async def review(path: Path | None, limit: int, conflicts_only: bool) -> None:
    store = await Store.open(path)
    try:
        where = "WHERE f.conflict_with IS NOT NULL" if conflicts_only else ""
        async with store.db.execute(
            f"SELECT f.id, f.subject, f.text, f.source_kind, f.confidence, f.ts,"
            f" f.conflict_with, a.text, a.speaker, a.source"
            f" FROM facts f LEFT JOIN atoms a ON a.id = f.source_atom_id"
            f" {where} ORDER BY f.subject, f.ts DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()

        if not rows:
            print("no facts yet — run the fact pass first")
            return

        current = None
        for (fid, subject, text, kind, confidence, ts, conflict,
             atom_text, speaker, source) in rows:
            if subject != current:
                current = subject
                print(f"\n── {subject} " + "─" * max(0, 58 - len(subject)))
            when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            flag = f"  ⚠ conflicts with fact {conflict}" if conflict else ""
            print(f"  [{fid}] {text}")
            print(f"      {kind}  conf={confidence:.2f}  {when}{flag}")
            if atom_text:
                print(_wrap(f'from [{speaker}/{source}]: "{atom_text}"'))
            else:
                print("      (source atom missing)")

        from lyra_memory.store.passes.facts import FactPass

        stats = await FactPass(store, llm=None).instrumentation()
        print(f"\n── {len(rows)} shown · "
              f"{stats['unresolved_conflicts']} unresolved conflicts")
        if stats["crowded_subjects"]:
            print(f"   crowded subjects (consolidation may be under-firing): "
                  f"{', '.join(stats['crowded_subjects'])}")
    finally:
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=None, help="store path")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--conflicts", action="store_true",
                        help="show only facts with a conflict flag")
    args = parser.parse_args()
    asyncio.run(review(args.path, args.limit, args.conflicts))


if __name__ == "__main__":
    main()
