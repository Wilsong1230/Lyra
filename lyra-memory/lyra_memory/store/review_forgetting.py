"""Show what forgetting demoted, and why, for a manual read.

Step 11's verification is "demoted atoms are ones you'd expect". That is a
judgement about her history, not a property of the arithmetic, so the only
honest check is to read the list.

    python -m lyra_memory.store.review_forgetting
    python -m lyra_memory.store.review_forgetting --survivors   # the other end

Each row shows the three inputs — age, salience, times retrieved — so a
surprising demotion can be traced to which one caused it. Read for:

  * Anything you would be sorry to lose from recall. Nothing is deleted, so
    this is recoverable, but it is the signal that a constant is wrong.
  * Anything demoted purely for age that keeps proving useful. That means
    access is not being weighted enough.
  * Anything surviving purely because it was retrieved once by accident.

Tune by reading. Adjusting constants until the histogram looks right is how
you get a curve that fits nothing in particular.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import textwrap
import time
from pathlib import Path

from lyra_memory.config import FORGET_THRESHOLD
from lyra_memory.store import Store

_DAY = 86400.0


async def review(path: Path | None, limit: int, survivors: bool) -> None:
    store = await Store.open(path)
    try:
        async with store.db.execute(
            "SELECT CAST(j.value AS INTEGER) AS atom_id, COUNT(*)"
            " FROM context_log c, json_each(c.atom_ids) j GROUP BY atom_id"
        ) as cur:
            access = {row[0]: row[1] for row in await cur.fetchall()}

        comparison = ">=" if survivors else "<"
        order = "DESC" if survivors else "ASC"
        async with store.db.execute(
            f"SELECT id, ts, speaker, text, salience, retrievability FROM atoms"
            f" WHERE retrievability IS NOT NULL AND retrievability {comparison} ?"
            f" ORDER BY retrievability {order} LIMIT ?",
            (FORGET_THRESHOLD, limit),
        ) as cur:
            rows = await cur.fetchall()

        async with store.db.execute(
            "SELECT COUNT(*), SUM(retrievability < ?) FROM atoms"
            " WHERE retrievability IS NOT NULL",
            (FORGET_THRESHOLD,),
        ) as cur:
            total, demoted = await cur.fetchone()

        if not rows:
            print("nothing scored yet — run the forgetting pass first")
            return

        heading = "STILL COMPETING" if survivors else "DEMOTED (out of recall)"
        print(f"── {heading} ─────────────────────────────────────")
        now = time.time()
        for atom_id, ts, speaker, text, salience, score in rows:
            age = (now - ts) / _DAY
            print(f"\n  [{atom_id}] r={score:.3f}  age={age:.0f}d  "
                  f"salience={salience if salience is not None else float('nan'):.2f}  "
                  f"retrieved={access.get(atom_id, 0)}x")
            print(textwrap.fill(f"{speaker}: {text}", width=88,
                                initial_indent="      ", subsequent_indent="      "))

        print(f"\n── {demoted or 0} of {total} scored atoms are below "
              f"{FORGET_THRESHOLD} and out of the recall competition.")
        print("   None are deleted. All remain queryable by time, session, "
              "entity, and exact id.")
    finally:
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--survivors", action="store_true",
                        help="show the most retrievable atoms instead")
    args = parser.parse_args()
    asyncio.run(review(args.path, args.limit, args.survivors))


if __name__ == "__main__":
    main()
