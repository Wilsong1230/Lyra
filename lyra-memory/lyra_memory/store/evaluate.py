"""Retrieval-quality harness.

`context_log` records *what* was injected, not whether it was right. Without a
score, similarity floors and budgets get tuned blind — every change feels like
an improvement because nothing contradicts it.

This is the minimum viable answer: a labeled set of turns, and three numbers.
For each turn — was the recall block relevant, was anything obviously missing,
did a miss fire when it shouldn't have.

Not rigorous. Sufficient to catch regressions, which is the entire claim.

    python -m lyra_memory.store.evaluate eval/retrieval_baseline.json

Run it before and after any retrieval change. Record the numbers *before*
tuning anything, or the tuning has nothing to argue with.

Against a throwaway store (the default) the labeled set supplies its own
corpus, seeded fresh each run. Pass --store to score against real
accumulated turns instead — the daemon's live store.db, say — in which case
the labeled set's "corpus" is ignored (there is nothing to seed; the store
already has whatever it has) and only its "turns" queries are scored:

    python -m lyra_memory.store.evaluate eval/retrieval_baseline.json \\
        --store ~/.lyra/store.db
"""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from lyra_memory.store import Store
from lyra_memory.store.context import build_context


@dataclass
class TurnResult:
    query: str
    found: list[str]
    missing: list[str]
    leaked: list[str]
    expect_miss: bool
    missed: bool
    recall_text: str

    @property
    def spurious_miss(self) -> bool:
        """A miss fired on a query the store can actually answer."""
        return self.missed and not self.expect_miss and not self.missing

    @property
    def spurious_injection(self) -> bool:
        """Something was injected for a query the store has nothing for."""
        return self.expect_miss and bool(self.recall_text.strip())


@dataclass
class Report:
    turns: list[TurnResult]

    @property
    def coverage(self) -> float:
        """Of everything labeled relevant, how much actually surfaced."""
        total = sum(len(t.found) + len(t.missing) for t in self.turns)
        return sum(len(t.found) for t in self.turns) / total if total else 1.0

    @property
    def leak_rate(self) -> float:
        """Turns where something labeled irrelevant was injected."""
        scored = [t for t in self.turns if not t.expect_miss]
        return sum(bool(t.leaked) for t in scored) / len(scored) if scored else 0.0

    @property
    def spurious_miss_rate(self) -> float:
        scored = [t for t in self.turns if not t.expect_miss]
        return sum(t.spurious_miss for t in scored) / len(scored) if scored else 0.0

    @property
    def spurious_injection_rate(self) -> float:
        expected = [t for t in self.turns if t.expect_miss]
        return sum(t.spurious_injection for t in expected) / len(expected) if expected else 0.0

    def render(self) -> str:
        lines = ["", "── retrieval baseline ──────────────────────────────────────"]
        for t in self.turns:
            if t.expect_miss:
                mark = "!" if t.spurious_injection else "."
                detail = "injected anyway" if t.spurious_injection else "correctly empty"
            elif t.missing:
                mark = "x"
                detail = f"missing {t.missing}"
            elif t.leaked:
                mark = "~"
                detail = f"leaked {t.leaked}"
            else:
                mark = "."
                detail = "ok"
            lines.append(f"  [{mark}] {t.query[:52]:<52} {detail}")

        lines += [
            "",
            f"  coverage                 {self.coverage:.2f}  "
            "(labeled-relevant material that surfaced)",
            f"  leak rate                {self.leak_rate:.2f}  "
            "(turns injecting something labeled irrelevant)",
            f"  spurious miss rate       {self.spurious_miss_rate:.2f}  "
            "(miss fired though the store had the answer)",
            f"  spurious injection rate  {self.spurious_injection_rate:.2f}  "
            "(injected for a query the store cannot answer)",
            "",
            f"  {len(self.turns)} turns",
        ]
        return "\n".join(lines)


async def evaluate(labeled: dict, store: Store | None = None) -> Report:
    """Seed a throwaway store from the corpus, then score every labeled turn."""
    owned = store is None
    if owned:
        tmpdir = Path(tempfile.mkdtemp(prefix="lyra-eval-"))
        store = await Store.open(tmpdir / "store.db")
        base = time.time() - 100_000
        for i, atom in enumerate(labeled["corpus"]):
            await store.append_atom(
                speaker=atom["speaker"], source="cli", text=atom["text"], ts=base + i)

    try:
        results = []
        for spec in labeled["turns"]:
            # recent_turns=0: the verbatim block would otherwise answer every
            # query about the most recent atoms and hide what recall did.
            ctx = await build_context(store, query=spec["query"], recent_turns=0)
            recall = ctx.blocks.get("recall", "")
            found = [m for m in spec.get("relevant", []) if m.lower() in recall.lower()]
            missing = [m for m in spec.get("relevant", []) if m.lower() not in recall.lower()]
            leaked = [m for m in spec.get("irrelevant", []) if m.lower() in recall.lower()]
            results.append(TurnResult(
                query=spec["query"], found=found, missing=missing, leaked=leaked,
                expect_miss=spec.get("expect_miss", False),
                missed=any("semantic" in m for m in ctx.misses),
                recall_text=recall,
            ))
        return Report(results)
    finally:
        if owned:
            await store.close()


async def _run(labeled: dict, store_path: Path | None) -> Report:
    if store_path is None:
        return await evaluate(labeled)
    store = await Store.open(store_path)
    try:
        return await evaluate(labeled, store=store)
    finally:
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labeled", type=Path, help="path to a labeled JSON set")
    parser.add_argument(
        "--store", type=Path, default=None, dest="store_path",
        help="score against this store.db (e.g. the daemon's live store) "
             "instead of a throwaway store seeded from the labeled set's "
             "own corpus",
    )
    args = parser.parse_args()

    labeled = json.loads(args.labeled.read_text())
    report = asyncio.run(_run(labeled, args.store_path))
    print(report.render())


if __name__ == "__main__":
    main()
