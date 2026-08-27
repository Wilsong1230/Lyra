# `lyra_memory.store` — the rebuilt memory

**Invariant: the hot path appends only. Everything structural is nullable,
cold, and derived.** A cold pass crashing leaves the store correct, because
the atoms it reads were permanent before it started.

This is a separate store from the old four-layer system, in its own file
(`~/.lyra/store.db`). `~/.lyra/memory.db` and the `lyra_memory` top-level
modules still run untouched — the two schemas share table names but not
columns, and the sheet is explicit that there is no migration.

## Layout

| module | what it does |
|---|---|
| `schema.py` | the DDL, `SCHEMA_VERSION`, and the enforced vocabularies |
| `integrity.py` | boot-time assertion: structure, version, embedding space |
| `__init__.py` | `Store` — the hot append path, `runs`, `record_outcome` |
| `context.py` | context assembly: five blocks, budgets, three recall paths |
| `evaluate.py` | retrieval-quality harness (`eval/retrieval_baseline.json`) |
| `review_facts.py` | operator tool — extracted facts beside their source atoms |
| `review_forgetting.py` | operator tool — what got demoted, and why |
| `passes/cold.py` | `ColdPass`: backs up before every run |
| `passes/dream.py` | reflection → `dreams` + `dream_atoms`, trait candidates |
| `passes/segmentation.py` | `find_boundaries` (pure) → `sessions`, `gap_since_prev` |
| `passes/salience.py` | outcomes → `atoms.salience` |
| `passes/entities.py` | `atoms.text` → `entities`, `atom_entities` |
| `passes/facts.py` | durable claims → `facts`, conflict flags only |
| `passes/commitments.py` | open loops, evidence-only closure |
| `passes/clustering.py` | co-occurring entities → `entities(kind='project')` |
| `passes/forgetting.py` | `atoms.retrievability` — demotion, never deletion |

## The hot path

```python
store = await Store.open()
vec = await embed(user_text)                       # ~10ms
ctx = await build_context(store, user_text, query_vec=vec, synthesize=llm)
reply = await model(ctx.text, user_text)           # the LLM call
await store.ingest_turn(user_text=user_text, lyra_text=reply,
                        injected=ctx.as_log_row(), user_vec=vec)
```

Budget is <50ms excluding the LLM call. Measured at p50 2.0ms / max 8.8ms over
50 turns on the offline embedder; real MiniLM adds roughly 10ms per embed, and
the query embedding is computed once and reused.

## Things that will look like bugs and are not

- **`SEGMENTATION_USE_EMBEDDINGS` is `False`.** It was measured and it made
  segmentation worse (precision 1.00 → 0.89). See the docstring; a test fails
  if that stops being true, which is a prompt to re-measure, not to delete it.
- **Salience ignores the sign of valence.** A failure is as memorable as a
  success. Asserted deliberately.
- **v1 never writes `superseded_by` or `valid_until`.** Conflicts are flagged
  and both rows stay live. Over-supersession erases true things invisibly.
- **Opening an existing store never runs the DDL.** Silently repairing drift
  destroys the question of *when* it drifted.
- **Everything marked `PROVISIONAL` in `config.py`** was chosen for mechanism
  and never calibrated against real MiniLM distances. See MANUAL.md.

Rationale for every non-obvious choice is in `../DECISIONS.md`; anything that
still has to be run by hand is in `../MANUAL.md`.
