"""Test-time embedding backend selection.

Opt-in only (CP-B, change 8). This file used to probe whether
all-MiniLM-L6-v2 was cached and silently set LYRA_EMBED_BACKEND=hashed on a
cache miss — convenient, but it made a genuinely missing model
indistinguishable from a deliberate offline choice, in tests and (had the
same probe ever been reused there) on the daemon's own startup path, which
change 8 requires to fail loudly instead. See lyra_core.runtime.Runtime.start
for the daemon-side half of this: an embed("warmup") call it does not
catch-and-fall-back from.

There is nothing left to do here: lyra_memory.embeddings reads
LYRA_EMBED_BACKEND from the environment at call time on its own. Set it
yourself to run offline —

    LYRA_EMBED_BACKEND=hashed python -m pytest

— and leave it unset to require the real model, cached or not.
"""
from __future__ import annotations
