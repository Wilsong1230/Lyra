#!/usr/bin/env python3
"""tools/dedup_probe.py — CP-E offline dedup oracle.

Takes a `candidates` table from any sqlite file that has one — the old
db.py-shaped `memory.db` and the new store/schema.py-shaped `store.db` use
the identical column set (id, trait_name, trait_value, evidence_count,
last_seen, category, evidence_text), so one script reads both without
knowing which schema it is looking at. Opened read-only (`mode=ro`); this
script never writes anything.

For each candidate it embeds two different texts — the label (trait_name)
and the description (trait_value) — through the same embedding backend and
the same CANDIDATE_DEDUP_THRESHOLD the live pool (candidate_pool.py) uses,
and reports, as numbers, for EACH embedding method separately:

  - the pairwise L2 distance distribution (n, min, max, mean, median)
  - how many distinct groups the candidates fall into at the current
    threshold
  - the size of the largest group

Grouping here is connected components of the pairwise-below-threshold
graph — every candidate within `threshold` of another (directly, or
transitively through a chain of near neighbours) is one group. That is
deliberately NOT a replay of CandidatePool's own online insertion process
(nearest-of-what-exists-so-far, with the stored vector drifting to a
running centroid after every merge — order-dependent, see
candidate_pool.py). This script's job is to say how many groups the DATA
supports at this threshold, independent of any particular insertion order;
CandidatePool's live behaviour is a different, order-sensitive question
this script does not answer.

It also reports one bonus measurement past change 5's two: how many groups
EXACT trait_name matching yields — this is what the live pool actually
uses for the closed (template-generated) vocabulary, see
CandidatePool.add_observation(closed_vocabulary=True) and DECISIONS.md
(CP-E, change 2) — so a reader can see, side by side, why that vocabulary
does not use either embedding method above.

Runs with no daemon, no Store, no CognitiveCore — a bare sqlite file and
lyra_memory.embeddings/config are the whole dependency surface, matching
tools/affect_probe.py's shape (stdlib-first, offline, no test harness).

    lyra_ai/venv/bin/python3 tools/dedup_probe.py path/to/candidates.db

Needs a venv with lyra_memory installed (lyra_ai/venv or lyra-memory/venv —
either has it, see bootstrap.sh); lyra_memory/__init__.py imports aiosqlite
unconditionally even though this script's own imports (embeddings, config)
do not need it, so bare system python3 will not find the package. Not
worked around here — that import shape is outside this checkpoint's FILES.

Set LYRA_EMBED_BACKEND=hashed explicitly to force the offline stand-in
(deterministic, surface-level only — see lyra_memory/embeddings.py); left
unset, this script probes for a locally cached all-MiniLM-L6-v2 the same
way lyra-memory/tests/conftest.py does and falls back to the stand-in only
if the real model is not already on disk (this container has no network
access to fetch it on first use).
"""
from __future__ import annotations

import argparse
import asyncio
import math
import os
import sqlite3
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LYRA_AI = REPO_ROOT / "lyra_ai"
if str(LYRA_AI) not in sys.path:
    sys.path.insert(0, str(LYRA_AI))


def _select_embedding_backend() -> None:
    """Must run before lyra_memory.embeddings is ever imported (the module
    reads LYRA_EMBED_BACKEND at import time). Mirrors
    lyra-memory/tests/conftest.py's _real_model_is_cached(): prefer the real
    model, fall back to the offline stand-in only if it truly is not
    reachable with no network call, and never override an explicit choice
    the caller already made via the environment."""
    if "LYRA_EMBED_BACKEND" in os.environ:
        return
    previous = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        from lyra_memory.config import EMBED_MODEL
        from sentence_transformers import SentenceTransformer

        SentenceTransformer(EMBED_MODEL)
        real_available = True
    except Exception:
        real_available = False
    finally:
        if previous is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = previous
    if not real_available:
        os.environ["LYRA_EMBED_BACKEND"] = "hashed"


_select_embedding_backend()

from lyra_memory.config import CANDIDATE_DEDUP_THRESHOLD, EMBED_DIM  # noqa: E402
from lyra_memory.embeddings import embed  # noqa: E402

# Same conversion candidate_pool.py uses: cosine_dist = L2_dist^2 / 2 for
# normalized vectors, so L2_threshold = sqrt(2 * cosine_threshold).
L2_THRESHOLD = math.sqrt(2 * CANDIDATE_DEDUP_THRESHOLD)


def load_candidates(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, trait_name, trait_value FROM candidates ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [{"id": r[0], "trait_name": r[1], "trait_value": r[2]} for r in rows]


def _unpack(raw: bytes) -> list[float]:
    return list(struct.unpack(f"{EMBED_DIM}f", raw))


def _l2(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


async def embed_all(texts: list[str]) -> list[list[float]]:
    return [_unpack(await embed(t)) for t in texts]


def pairwise_distances(vectors: list[list[float]]) -> list[float]:
    out = []
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            out.append(_l2(vectors[i], vectors[j]))
    return out


def group_by_threshold(vectors: list[list[float]], threshold: float) -> list[list[int]]:
    """Connected components of the pairwise-below-threshold graph."""
    n = len(vectors)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if _l2(vectors[i], vectors[j]) < threshold:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def group_by_exact_name(candidates: list[dict]) -> list[list[int]]:
    groups: dict[str, list[int]] = {}
    for c in candidates:
        groups.setdefault(c["trait_name"], []).append(c["id"])
    return list(groups.values())


def _dist_summary(dists: list[float]) -> str:
    if not dists:
        return "n=0 (fewer than 2 candidates)"
    s = sorted(dists)
    median = s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2
    return (f"n={len(dists)} min={min(dists):.4g} max={max(dists):.4g} "
            f"mean={sum(dists) / len(dists):.4g} median={median:.4g}")


def report(db_path: Path, candidates: list[dict], label_vecs, desc_vecs) -> str:
    lines = [f"=== dedup_probe: {db_path} ===", f"candidates: {len(candidates)}"]
    if not candidates:
        lines.append("(no candidates in this store — nothing to group)")
        return "\n".join(lines)

    lines.append(
        f"CANDIDATE_DEDUP_THRESHOLD (cosine) = {CANDIDATE_DEDUP_THRESHOLD:g}  "
        f"->  L2 threshold = {L2_THRESHOLD:.6g}"
    )

    for method_name, vecs in (("label (trait_name)", label_vecs), ("description (trait_value)", desc_vecs)):
        dists = pairwise_distances(vecs)
        groups = group_by_threshold(vecs, L2_THRESHOLD)
        sizes = sorted((len(g) for g in groups), reverse=True)
        lines.append(f"\n-- embedding: {method_name} --")
        lines.append(f"  pairwise L2 distance: {_dist_summary(dists)}")
        lines.append(f"  distinct groups at threshold: {len(groups)}")
        lines.append(f"  largest group size: {sizes[0] if sizes else 0}")

    exact_groups = group_by_exact_name(candidates)
    exact_sizes = sorted((len(g) for g in exact_groups), reverse=True)
    lines.append("\n-- bonus: exact trait_name match (what the live closed vocabulary actually uses) --")
    lines.append(f"  distinct groups: {len(exact_groups)}")
    lines.append(f"  largest group size: {exact_sizes[0] if exact_sizes else 0}")

    return "\n".join(lines)


async def _run(db_path: Path) -> str:
    candidates = load_candidates(db_path)
    if not candidates:
        return report(db_path, candidates, [], [])
    label_vecs = await embed_all([c["trait_name"] for c in candidates])
    desc_vecs = await embed_all([c["trait_value"] for c in candidates])
    return report(db_path, candidates, label_vecs, desc_vecs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "db", type=Path,
        help="path to a sqlite file with a `candidates` table (memory.db- or store.db-shaped)",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"no such file: {args.db}", file=sys.stderr)
        return 1

    text = asyncio.run(_run(args.db))
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
