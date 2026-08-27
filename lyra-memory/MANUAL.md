# MANUAL.md — steps to run by hand

Nothing in this build runs destructive commands against `~/.lyra`, and nothing
here has been run for you. Anything that would touch the live store, or that
could not be done in the build container, lands here.

---

## 1. Cache the embedding model (required for full verification)

The build container's egress proxy denies `huggingface.co` (403 on CONNECT),
so `all-MiniLM-L6-v2` could not be downloaded. Tests fall back to a
deterministic offline stand-in, and the three tests that assert real semantics
**skip** rather than pass falsely.

On a machine with network access to huggingface.co:

```bash
cd lyra-memory
./venv/bin/python -c "
from sentence_transformers import SentenceTransformer
SentenceTransformer('all-MiniLM-L6-v2')
print('cached')
"
./venv/bin/python -m pytest -q          # header should read: embeddings: real (all-MiniLM-L6-v2)
```

Expected after caching: 0 skips. Confirm the header says `embeddings: real`
before trusting any retrieval-quality number.

## 2. Re-tune retrieval constants against real MiniLM distances

Every similarity floor and dedupe threshold introduced from step 3 onward was
chosen for mechanism, not calibrated against real embedding distances (see
DECISIONS.md, "Build environment"). After step 1 above, re-measure on the real
store and adjust. They are flagged `PROVISIONAL` in `config.py`.

## 3. Backup before every cold pass

Hard rule: back up before each cold pass, 30-day retention. Not automated
here, and deliberately not run against `~/.lyra` from the build:

```bash
mkdir -p ~/.lyra/backups
sqlite3 ~/.lyra/memory.db ".backup '$HOME/.lyra/backups/memory-$(date +%Y%m%dT%H%M%S).db'"
# retention: 30 days, NOT 7 — undetected tampering contaminates every backup
# in its window, so retention must exceed plausible detection lag.
find ~/.lyra/backups -name 'memory-*.db' -mtime +30 -print   # review, then -delete
```

Review the `-print` output before ever adding `-delete`.
