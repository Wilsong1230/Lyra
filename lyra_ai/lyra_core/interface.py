"""lyra_core.interface — Phase 0 contract.

Defines the three port types (Observation, Intent, AffectState) and the
CognitiveCore stub.  The reserved seams documented here are load-bearing:
changing field names or types later requires touching every peripheral.

Reserved seams present in Phase 0 (inert until noted phase):
  ObservationKind.external_affect   — empathy channel (Phase ?)
  Observation.predicted / .actual   — competence drive (Phase 3)
  Observation.affect_hint           — Wilson's sensed valence/arousal (Phase ?)
  AffectVector.control              — third affect axis / dominance (Phase ?)
  AffectState.mood                  — medium-term affect offset (Phase ?)
  AffectState.temperament           — trait-level time constants (Phase ?)
  IntentKind.research               — deliberate information-seeking (Phase ?)
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# CP-D.0: module-level, not deferred like the affect-fact duck-typing below —
# retrieve_context()/ingest_exchange() are inherently Store-shaped (they call
# Store's own build_context()/ingest_turn()), not memory-implementation-
# agnostic like the rest of CognitiveCore, so there is no genericity to
# preserve by deferring this one. Cheap to import: store.context only
# type-imports the heavy embedding model lazily, inside embed() itself.
from lyra_memory.candidate_pool import CandidatePool
from lyra_memory.store.context import build_context as _build_context


# ── Observation kinds ─────────────────────────────────────────────────────────

class ObservationKind(str, Enum):
    sensory          = "sensory"           # perception of the world
    action_outcome   = "action_outcome"    # result of an emitted intent
    external_affect  = "external_affect"   # RESERVED — empathy seam (Wilson's state)


# ── Intent kinds ──────────────────────────────────────────────────────────────

class IntentKind(str, Enum):
    speak      = "speak"
    look       = "look"
    set_state  = "set_state"
    noop       = "noop"
    research   = "research"   # RESERVED — deliberate information-seeking
    retrieval  = "retrieval"  # CP-D: retrieve context for the turn in progress
    repo_query = "repo_query" # CP-G: answer over indexed repo commit history


# ── Port types ────────────────────────────────────────────────────────────────

@dataclass
class Observation:
    """Uniform event shape for everything arriving at the core (IN port).

    Reserved fields are accepted and stored but unused until their phase:
      predicted / actual  — Phase 3 competence drive
      affect_hint         — empathy seam; only meaningful on external_affect obs
    """
    kind:    ObservationKind
    source:  str
    content: str
    ts:      float = field(default_factory=time.time)

    # Reserved: prediction-outcome pairing for the competence drive (Phase 3).
    # An action_outcome observation may carry what was expected vs what happened.
    # NOTE: str | None is Phase 0 convenience only. The eventual competence-drive
    # shape is expected to be STRUCTURED (a value/confidence or distribution that
    # can be diffed into a numeric prediction-error magnitude). String is a
    # placeholder — not a decided contract.
    predicted: str | None = None
    actual:    str | None = None

    # Reserved: sensed valence/arousal of Wilson (external_affect only).
    # Shape TBD; stored as a free dict until the empathy channel is specified.
    affect_hint: dict | None = None


@dataclass
class Intent:
    """What the core wants to do (OUT port).

    The core emits intent only — never waveforms, hex colors, or raw audio.
    Peripherals translate intent into physical action.
    """
    kind:    IntentKind
    payload: dict
    ts:      float = field(default_factory=time.time)


@dataclass
class AffectVector:
    """A point in affect space: valence × arousal × control.

    control is the third axis (dominance / felt agency) — reserved until Phase ?.
    """
    valence: float       = 0.0
    arousal: float       = 0.0
    control: float | None = None   # RESERVED third axis


@dataclass
class AffectState:
    """How the core is feeling right now (OUT port).

    Three-timescale structure:
      emotion      — fast, current-moment vector  (Phase 0+, always set)
      mood         — medium-term offset            (RESERVED, None until Phase ?)
      temperament  — trait-level time constants    (RESERVED, None until Phase ?)

    The top-level valence / arousal / control properties delegate to emotion
    so that Phase 1+ can blend timescales without renaming the public surface.
    """
    emotion:      AffectVector        = field(default_factory=AffectVector)
    mood:         AffectVector | None = None   # RESERVED
    temperament:  AffectVector | None = None   # RESERVED

    @property
    def valence(self) -> float:
        return self.emotion.valence

    @property
    def arousal(self) -> float:
        return self.emotion.arousal

    @property
    def control(self) -> float | None:
        return self.emotion.control


# ── Core ──────────────────────────────────────────────────────────────────────

# Recurrence id used to route action_outcome failures into RelationalDrive —
# "the recurring problem" framing for repeated action failures (Phase 3.6).
_FAILURE_PROBLEM_ID = "action_outcome_failure"

# CP-B: obs.source names WHO/WHAT produced an observation (conversation / lyra
# / vision / ...), which is a different axis from Store's `speaker` vocabulary
# (wilson | lyra | system) and `source` channel vocabulary (cli | wakeword |
# ambient | vision | sandbox_read) — see DECISIONS.md (CP-B) for why this
# mapping is what it is. Everything that isn't her own turn and isn't a vision
# observation is attributed to the daemon's one channel today: the CLI.
_ATOM_SPEAKER_BY_OBS_SOURCE: dict[str, str] = {"conversation": "wilson", "lyra": "lyra"}
_ATOM_SOURCE_BY_OBS_SOURCE: dict[str, str] = {"vision": "vision"}
_DEFAULT_ATOM_SOURCE = "cli"

# CP-B: affect_state has no home in Store's schema (it is not a semantic fact
# about the world, and OUT OF SCOPE forbids adding a table for it) — persisted
# as a `facts` row instead, under a subject no real query will ever type. See
# DECISIONS.md (CP-B, "affect_state has no table of its own").
_AFFECT_STATE_FACT_SUBJECT = "_lyra_internal_affect_state"

# CP-D: full strength — retrieval is proposed whenever a turn is in progress
# (see tick()) and is overridden only by the same frustration-flip every
# drive-produced intent already goes through (action_selection.py). Not
# tuned; 1.0 is "yes, unless frustration is high enough to say no" in the
# same units ActionSelector already compares boredom/relational pressure
# against. See DECISIONS.md for what a 20-turn live run actually did with
# this value — whether decline is reachable at this pressure is itself part
# of what this checkpoint measures, not something assumed going in.
_RETRIEVAL_PRESSURE = 1.0

# CP-I: same reasoning as _RETRIEVAL_PRESSURE, same value, for the same
# structural reason — a candidate that either matches the keyword set at
# full strength or isn't a candidate at all, scored against frustration by
# ActionSelector exactly like retrieval. Not tuned relative to retrieval:
# equal pressure means a turn frustrated enough to decline retrieval is
# also frustrated enough to decline repo_query, with no separate lever for
# "decline repo reads but not conversational recall" — CHANGES did not ask
# for one, and OUT OF SCOPE forbids new drives, which a second, different
# pressure constant would start to resemble.
_REPO_QUERY_PRESSURE = 1.0

# CP-D: the two-branch weak signal a retrieval outcome reduces to (change 4)
# — whether the assembled context contained at least one atom above the
# retrievability floor. Mirrors development.py's trait_name_from_outcome in
# shape (a fixed, closed vocabulary of exactly two outcomes), not reused
# from it: that function is keyed to the boredom/relational "success"
# concept (predicted == actual), which retrieval has no equivalent of.
def _retrieval_trait_from_outcome(had_context: bool) -> tuple[str, str]:
    if had_context:
        return (
            "retrieval finds relevant context",
            "an assembled context contained at least one atom above the retrievability floor",
        )
    return (
        "retrieval finds nothing",
        "an assembled context contained no atoms above the retrievability floor",
    )


# CP-D.0: obs.source values whose text is NOT written here — they are the two
# halves of one exchange, persisted together (with the retrieval that
# informed them) by ingest_exchange() -> Store.ingest_turn(), not as two
# separate, un-transacted append_atom() calls. See _ingest_sensory and
# DECISIONS.md (CP-D.0, item on atomicity). Every OTHER sensory source
# (vision, ...) is unaffected and still persists here, one atom per tick,
# exactly as before.
_EXCHANGE_OBS_SOURCES: frozenset[str] = frozenset(_ATOM_SPEAKER_BY_OBS_SOURCE)

# CP-D: the context_log.misses entry that marks a turn which declined to
# retrieve — distinct from a "semantic:"/"lexical:" miss (which mean
# retrieval ran and found nothing) so report.py can tell "didn't try" apart
# from "tried and found nothing." Change 3's own requirement: a declined
# turn is still a visible row, not an absent one.
DECLINED_MARKER = "retrieval: declined"

# ── CP-G: repo query ────────────────────────────────────────────────────────
#
# The `facts` row shape a repo_index.py commit becomes (change 1 — see
# DECISIONS.md for the full comparison against atoms/entities):
#   subject      = the commit's full 40-hex-char hash (idempotency key —
#                   query existing subjects to skip already-indexed commits;
#                   also the citation-validation key, via a prefix LIKE).
#   text         = "[<short-hash>] <date> <author>: <subject-line>" — already
#                   in the exact bracketed-citation form the repo-context
#                   block displays, so retrieval does no reformatting.
#   source_kind  = "repo_commit" — not one of FACT_CONFIDENCE's four values
#                  (stated/observed/document/inferred); source_kind has no DB
#                  CHECK constraint (schema.py enforces `source`/`environment`
#                  vocab in Python for the same reason — a growing vocabulary
#                  cannot be a CHECK constraint under a no-migration schema),
#                  so this needed no schema change, matching OUT OF SCOPE.
#   confidence   = 1.0 — a commit hash either is or is not in `git log`; there
#                  is no epistemic gradient to weigh, unlike a stated/
#                  inferred fact about the world.
#   source_atom_id = NULL — not derived from any atom she experienced.
#   valid_from   = the commit's own author timestamp; ts = indexing time —
#                  the schema's existing distinction between "when this
#                  became true" and "when this row was written" is exactly
#                  right for a commit indexed long after it happened.
#
# Distinguishable from a lived experience: a `facts` row with source_kind=
# "repo_commit" is never atoms-table content, so it is structurally
# unreachable by DreamPass._input_atoms() (reads `atoms` only) — a commit
# cannot become dream input, a candidate, or a promoted trait no matter what
# it says, the same "cannot write to her identity" property
# DREAM_EXCLUDED_SOURCES gives sandbox_read atoms, achieved here for free by
# never being an atom in the first place.
#
# Excluded from ordinary conversational retrieval by construction, not by a
# new filter: store/context.py's `_facts_block` (OUT OF SCOPE forbids
# touching it) only injects a fact when a query word exactly matches its
# `subject` — and a repo commit's subject is a 40-hex-char hash, a string
# that essentially never appears as a token in ordinary conversation. Repo
# rows are retrieved by a wholly separate path — retrieve_repo_context()
# below, wired to a NEW intent kind, not the recall/semantic/lexical/
# temporal machinery `_recall_block` runs (which has no source filter at
# all and would have leaked commit rows into every conversational turn had
# this checkpoint used `atoms` instead — the reason `facts` was chosen).
REPO_COMMIT_SOURCE_KIND = "repo_commit"

# CP-G change 3, CP-I change 1: the keyword match still decides WHETHER
# repo_query is a candidate this tick, unchanged — what changed at CP-I is
# what happens to that candidate afterward. CP-G appended the intent
# directly to tick()'s output, bypassing ActionSelector entirely (action_
# selection.py was out of that checkpoint's scope); that made repo_query
# undeclinable — the only IntentKind whose proposal was also its
# execution, with no comparison to lose. CP-I feeds this exact same
# keyword-match result into `pressures["repo_query"]` (see tick() below)
# instead, so ActionSelector.select() scores it against frustration
# exactly as it already scores retrieval — see action_selection.py.
_REPO_QUERY_KEYWORDS: frozenset[str] = frozenset({
    "commit", "commits", "committed", "checkpoint", "checkpoints",
    "repo", "repository", "codebase",
})


def _looks_like_repo_query(observations: list["Observation"]) -> bool:
    """True iff this tick's conversational observation's text plausibly asks
    about the repo's commit history — a fixed keyword substring check
    against the "conversation" observation only (never her own "lyra" turn,
    same restriction retrieval's awaiting_reply already applies). CP-I:
    this is now a CANDIDACY test, not a decision — see tick()."""
    for obs in observations:
        if obs.kind == ObservationKind.sensory and obs.source == "conversation":
            text_lower = obs.content.lower()
            if any(kw in text_lower for kw in _REPO_QUERY_KEYWORDS):
                return True
    return False


# Bounds how many commit rows one repo-query turn injects — the analogue of
# CONTEXT_BUDGETS["recall"] (store/context.py, OUT OF SCOPE to touch) but a
# row count rather than a token budget: commit lines are short and uniform
# (one line each, already truncated to a single subject line at index time),
# so counting rows is simple and sufficient rather than needing a second
# token-budget mechanism duplicating context.py's.
_REPO_QUERY_ROW_LIMIT = 10

_HEX_TOKEN_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
_REPO_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
# CP-G change 4/5: the exact citation form repo commit `text` rows are
# stored in and the block instructs the model to use — bracketed hash,
# 7-40 lowercase hex chars (matches git's own abbreviation range up to a
# full hash). Parsed out of her reply for change 5's outcome check.
_CITATION_RE = re.compile(r"\[([0-9a-f]{7,40})\]")


@dataclass
class RepoContext:
    """What retrieve_repo_context() found — the repo-query analogue of
    lyra_memory.store.context.ContextResult, deliberately much smaller (one
    block, no budget accounting, no misses list): CHANGES scopes this
    checkpoint to commit metadata only, not a second context-assembly
    system."""
    text: str
    commit_hashes: list[str]


# CP-H change 1: the closed candidate vocabulary for a citation outcome.
# Exactly four branches, distinguished by two independent facts that must
# not collapse into each other:
#   (a) was there repo context to cite at all (commit rows retrieved this
#       turn)?
#   (b) if something WAS cited, did every cited hash exist in the index?
# "cited nothing because there was nothing to cite" and "cited nothing
# despite having context" are different facts about her behavior — one is
# about retrieval finding nothing, the other is about her declining to use
# what she was given — so they get distinct labels rather than a shared
# "nothing cited" bucket. Exact label match (closed_vocabulary=True), the
# same reasoning CP-D's frustration vocabulary and CP-G's retrieval
# vocabulary already established: these four descriptions are built from
# one template each, share nearly all their words with their neighbors,
# and a semantic threshold loose enough to merge genuine duplicates would
# merge these apart-in-meaning branches too (measured for both prior
# closed vocabularies in this codebase; not re-measured here because the
# shape — one template, boolean-flipped — is identical, not merely
# similar; see DECISIONS.md for why re-deriving the same conclusion a
# third time was not repeated).
def _repo_citation_trait_from_outcome(
    had_repo_context: bool, hit_count: int, miss_count: int,
) -> tuple[str, str]:
    if hit_count + miss_count == 0:
        if had_repo_context:
            return (
                "repo context given but nothing cited",
                "an executed repo-query turn retrieved commit rows from the"
                " index, but the reply cited none of them",
            )
        return (
            "no repo context to cite",
            "an executed repo-query turn retrieved no commit rows from the"
            " index, so the reply had nothing to cite",
        )
    if miss_count > 0:
        return (
            "repo citations include a false hash",
            "an executed repo-query turn's reply cited one or more hashes,"
            " and at least one cited hash was not found in the indexed"
            " commits",
        )
    return (
        "repo citations verified",
        "an executed repo-query turn's reply cited one or more hashes, and"
        " every cited hash was found in the indexed commits",
    )


class CognitiveCore:
    """The live cognitive loop.

    tick() is the single entry point: ingest observations into memory and the
    drives, advance affect, select intents (biased by promoted traits), and
    gate them. introspect() reads the live affect without advancing anything.

    Every component is constructed with real defaults but is injectable — the
    test harness can build a fully-faked core by passing fakes for any of
    affect / competence / boredom / relational / selector / consolidator /
    memory / gate.
    """

    def __init__(
        self,
        gate=None,
        affect=None,
        competence=None,
        boredom=None,
        relational=None,
        selector=None,
        consolidator=None,
        memory=None,
    ) -> None:
        from lyra_core.gate import HarmGate
        from lyra_core.affect import AffectEngine
        from lyra_core.drives import BoredomDrive, CompetenceTracker, RelationalDrive
        from lyra_core.action_selection import ActionSelector
        from lyra_core.development import OutcomeConsolidator

        self._gate = gate if gate is not None else HarmGate()
        self._affect = affect if affect is not None else AffectEngine()
        self._competence = competence if competence is not None else CompetenceTracker()
        self._boredom = boredom if boredom is not None else BoredomDrive(self._competence)
        self._relational = relational if relational is not None else RelationalDrive()
        self._selector = selector if selector is not None else ActionSelector()
        # CP-B: no default memory. The daemon always injects a Store (async
        # open, not constructible here — CognitiveCore.__init__ is sync); a
        # core built with no memory injected simply has none, and any ingest
        # that reaches it fails loudly rather than reaching for a store this
        # process never opened. See DECISIONS.md (CP-B, item 1).
        self._memory = memory

        self._consolidator_injected = consolidator is not None
        self._consolidator = consolidator if consolidator is not None else OutcomeConsolidator(
            pool=getattr(self._memory, "candidate_pool", None),
            working_memory=getattr(self._memory, "working_memory", None),
        )

        # CP-I: read by runtime.py after tick() to distinguish a repo_query
        # WIN (intent present in tick()'s output) from a LOSS (this is
        # nonzero but the intent isn't) from NOT-A-CANDIDATE (this is 0.0)
        # — see tick()'s own comment. 0.0 before the first tick: nothing
        # has proposed anything yet.
        self._last_repo_query_pressure: float = 0.0

    @property
    def memory(self):
        """The memory (a Store, in the daemon; whatever was injected in tests)."""
        return self._memory

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the owned memory system (if it has a start()) and restore
        persisted affect, if any.

        CP-B: Store is opened by the caller (Runtime.start — Store.open() is
        async and this constructor is sync, so it cannot happen here) and has
        no start() of its own; `getattr` keeps this call working for anything
        that still has the old MemorySystem-style lifecycle (fakes, tests)
        without requiring Store to grow a method it doesn't need.
        """
        start_fn = getattr(self._memory, "start", None)
        if start_fn is not None:
            await start_fn()

        structured_state = getattr(self._memory, "structured_state", None)
        if structured_state is not None:
            saved = await structured_state.get_fact("affect_state")
            if saved is not None:
                from lyra_core.affect import AffectEngine
                self._affect = AffectEngine.from_dict(saved)
        else:
            saved = await self._read_affect_fact()
            if saved is not None:
                from lyra_core.affect import AffectEngine
                self._affect = AffectEngine.from_dict(saved)

        if not self._consolidator_injected:
            from lyra_core.development import OutcomeConsolidator
            self._consolidator = OutcomeConsolidator(
                pool=getattr(self._memory, "candidate_pool", None),
                working_memory=getattr(self._memory, "working_memory", None),
            )

    async def stop(self) -> None:
        """Persist current affect state and stop the owned memory system.

        CP-B: Store has no stop() (Runtime closes it explicitly after this
        returns) — only MemorySystem-style memories are stopped here.
        """
        structured_state = getattr(self._memory, "structured_state", None)
        if structured_state is not None:
            await structured_state.set_fact("affect_state", self._affect.to_dict())
        else:
            await self._write_affect_fact()

        stop_fn = getattr(self._memory, "stop", None)
        if stop_fn is not None:
            await stop_fn()

    # ── affect persistence against a Store (no structured_state) ───────────────

    async def _read_affect_fact(self) -> dict | None:
        """Latest current affect_state row from Store's `facts` table, if any.

        Store has no generic key-value state table (OUT OF SCOPE forbids
        adding one) — see `_AFFECT_STATE_FACT_SUBJECT` and DECISIONS.md.
        """
        db = getattr(self._memory, "db", None)
        if db is None:
            return None
        async with db.execute(
            "SELECT text FROM facts WHERE subject = ? AND valid_until IS NULL"
            " ORDER BY id DESC LIMIT 1",
            (_AFFECT_STATE_FACT_SUBJECT,),
        ) as cur:
            row = await cur.fetchone()
        return json.loads(row[0]) if row is not None else None

    async def _write_affect_fact(self) -> None:
        db = getattr(self._memory, "db", None)
        if db is None:
            return
        now = time.time()
        await db.execute(
            "UPDATE facts SET valid_until = ? WHERE subject = ? AND valid_until IS NULL",
            (now, _AFFECT_STATE_FACT_SUBJECT),
        )
        await db.execute(
            "INSERT INTO facts (ts, subject, text, source_kind, confidence, valid_from)"
            " VALUES (?, ?, ?, 'inferred', 1.0, ?)",
            (now, _AFFECT_STATE_FACT_SUBJECT, json.dumps(self._affect.to_dict()), now),
        )
        await db.commit()

    # ── Ingest ───────────────────────────────────────────────────────────────

    async def _ingest_sensory(self, obs: Observation) -> None:
        # CP-D.0: "conversation" and "lyra" text is NOT written here anymore
        # — ingest_exchange() persists both halves of an exchange together,
        # atomically, with the retrieval that informed them (see
        # DECISIONS.md). Ticking one of these sources still advances drives
        # and affect below exactly as before; only the atom write moved.
        # This tick() call is still the caller's responsibility to pair with
        # an eventual ingest_exchange() — nothing here does that for them,
        # so a caller that ticks "conversation"/"lyra" without ever calling
        # ingest_exchange() silently writes no atom. TurnHandler.handle() is
        # currently the only such caller (see runtime.py).
        if obs.source in _EXCHANGE_OBS_SOURCES:
            return

        # SKIPPED DELIBERATELY, not by accident (CP-D.0 change 5): vision
        # observations still land here, via append_atom(), NOT ingest_turn().
        # ingest_turn() is structurally a pair — exactly one "her
        # interlocutor" atom and one "her" atom — and a vision description
        # is a third, unpaired atom with no lyra_text counterpart at the
        # moment it arrives (runtime.py's vision retry loop can fire zero or
        # several times before a final reply exists). Forcing it through
        # ingest_turn() would mean inventing a fake pairing OUT OF SCOPE has
        # no room for. See DECISIONS.md.
        #
        # Unguarded (CP-A; still true under CP-B's Store). A memory that is
        # not open, or a store that cannot take the write, is a turn that did
        # not happen; the daemon treats it as fatal rather than answering
        # over a store that is silently dropping her record.
        speaker = "system"
        source = _ATOM_SOURCE_BY_OBS_SOURCE.get(obs.source, _DEFAULT_ATOM_SOURCE)
        await self._memory.append_atom(speaker=speaker, source=source, text=obs.content)

    async def retrieve_context(self, query: str):
        """What Store would inject for `query` right now (CP-D.0) — read-only,
        against whatever atoms already exist. Call BEFORE the observations
        this turn will eventually write (via ingest_exchange), so retrieval
        never has to see — or exclude — its own turn's atoms.

        Returns a lyra_memory.store.context.ContextResult; pass it straight
        to ingest_exchange() afterward so what was actually retrieved is what
        gets logged, not a second, possibly different, computation of it.
        """
        return await _build_context(self._memory, query=query)

    async def ingest_exchange(
        self, user_text: str, lyra_text: str, context, session_id, source: str = "cli",
    ) -> tuple[int, int, int | None]:
        """Persist one full exchange — both atoms and the context_log row
        recording what retrieve_context() actually returned for it — in the
        one transaction Store.ingest_turn() provides (CP-D.0 change 2).

        `context` is the ContextResult retrieve_context() returned, or None
        when the turn declined to retrieve (CP-D change 3) — a declined turn
        still gets a context_log row, marked as such via a distinguished
        `misses` entry (`DECLINED_MARKER`) rather than being silently
        absent; report.py's `_context_log_section` recognizes it and counts
        it separately from a genuine empty retrieval.

        `session_id` is the daemon's own per-connection session string
        (TurnHandler.handle's `session` parameter) — Store's context_log
        schema declares this column INTEGER (a future cold-pass FK into
        `sessions`, which nothing populates yet), but SQLite's type affinity
        stores whatever is given it without error or schema violation; see
        DECISIONS.md for why reusing the session string here, rather than
        minting a new integer, is the honest choice — no cold-pass session
        row exists yet for this connection to reference.

        Returns (user_atom_id, lyra_atom_id, context_log_id). ingest_turn()
        itself does not return the context_log row's id (OUT OF SCOPE
        forbids changing it), so it is read back by the exact `ts` this call
        passed in — see _latest_context_log_id. None if it cannot be found
        (memory without a `.db`, e.g. a test fake).

        Unguarded, like _ingest_sensory: a failed write is a turn that did
        not happen, not a turn silently missing its record.

        SKIPPED DELIBERATELY (CP-D.0 change 5): `ingest_turn(user_vec=...)`
        exists to let the caller reuse an embedding it already computed
        rather than paying to embed user_text twice. retrieve_context()
        above has no way to hand one back — build_context() computes its
        own query vector internally and OUT OF SCOPE forbids changing its
        signature to expose it — so user_text is embedded again here. Not a
        choice this checkpoint could avoid; recorded so it reads as known,
        not missed. See DECISIONS.md.
        """
        if context is not None:
            injected = context.as_log_row()
        else:
            injected = {
                "atom_ids": [], "fact_ids": [], "dream_ids": [], "budget_used": 0,
                "misses": [DECLINED_MARKER],
            }
        injected["session_id"] = session_id
        now = time.time()
        user_atom_id, lyra_atom_id = await self._memory.ingest_turn(
            user_text=user_text, lyra_text=lyra_text, source=source, injected=injected, ts=now,
        )
        context_log_id = await self._latest_context_log_id(now)
        return user_atom_id, lyra_atom_id, context_log_id

    async def _latest_context_log_id(self, ts: float) -> int | None:
        db = getattr(self._memory, "db", None)
        if db is None:
            return None
        async with db.execute(
            "SELECT id FROM context_log WHERE ts = ? ORDER BY id DESC LIMIT 1", (ts,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row is not None else None

    async def record_retrieval_outcome(
        self, user_atom_id: int, context_log_id: int | None, atom_count: int, path: str,
    ) -> int | None:
        """One outcomes row per executed retrieval intent (CP-D change 4).

        The outcome bit is deliberately weak: whether the assembly returned
        at least one atom above the retrievability floor (atom_count > 0),
        encoded as `valence` (1.0/0.0) so it reads the same way every other
        outcome's success bit does. `record_outcome()`'s own columns have no
        room for atom_count/path/context_log_id as first-class fields (OUT
        OF SCOPE does not license changing Store's schema for them) — `path`
        goes in `actual` (Store's own free-text convention, e.g.
        lyra_core.outcomes' ENGAGEMENT/SILENCE), `predicted` names what a
        retrieval intent always wants ("context_available"), and
        `environment` carries context_log_id/atom_count as a small
        `key=value;...` string — parseable later, not a new column. See
        DECISIONS.md.
        """
        record_outcome = getattr(self._memory, "record_outcome", None)
        if record_outcome is None:
            return None
        valence = 1.0 if atom_count > 0 else 0.0
        return await record_outcome(
            intent_atom_id=user_atom_id,
            valence=valence,
            actual=path,
            predicted="context_available",
            environment=f"context_log_id={context_log_id};atom_count={atom_count}",
        )

    async def consolidate_retrieval_outcome(
        self, had_context: bool, outcome_id: int | None = None,
    ) -> tuple[str, str] | None:
        """Fires on a retrieval outcome (CP-E: routes through CandidatePool
        instead of CP-D's bare INSERT).

        CP-D's docstring here previously claimed Store had no equivalent of
        development.py's OutcomeConsolidator/CandidatePool. That was wrong,
        not merely outdated: `lyra_memory.candidate_pool.CandidatePool`
        already runs against this exact Store connection —
        `store/passes/dream.py`'s DreamPass constructs
        `CandidatePool(self.store.db)` for the dream-derived (open)
        vocabulary, and Store's own schema (`store/schema.py`) defines
        `vec_candidates` for it to use. See DECISIONS.md (CP-E, change 1)
        for the full read of that path before this change.

        `_retrieval_trait_from_outcome` is, by its own comment above,
        shaped exactly like development.py's `trait_name_from_outcome`: a
        fixed, two-branch, template-generated vocabulary. CandidatePool's
        `closed_vocabulary=True` mode exists precisely for that shape —
        OutcomeConsolidator already uses it for the frustration vocabulary,
        with a documented reason: descriptions of opposite branches from one
        template sit too close in embedding space to separate from genuine
        duplicates with any single threshold. Measured here too (see
        DECISIONS.md, change 2): the two retrieval descriptions
        ("...at least one atom above the retrievability floor" vs "...no
        atoms above the retrievability floor") sit well inside
        CANDIDATE_DEDUP_THRESHOLD under the offline embedding stand-in —
        embedding them would merge "finds context" and "finds nothing" into
        one candidate. So retrieval candidates dedup by exact trait_name
        (the label), not by embedding trait_value (the description); label
        text is what CandidatePool actually matches on for this vocabulary,
        by the same reasoning already established for the other closed
        vocabulary in this codebase, not a new exception invented here.

        Provenance (change 4): `evidence` is `outcome_id=<id>` when the
        caller has one, so a merged candidate's `evidence_text` accumulates
        one line per contributing `outcomes` row (CandidatePool now appends
        rather than replacing — see candidate_pool.py's _append_evidence).
        Parsing that back to `outcomes` rows is a store-side read, not a
        new column.

        Returns (trait_name, trait_value) on every call — CandidatePool
        inserts a new row on first sight of a branch and strengthens the
        existing one (evidence_count, last_seen, evidence_text) on every
        repeat, never a fresh row. Returns None only when memory has no
        `.db` (e.g. a test fake).
        """
        db = getattr(self._memory, "db", None)
        if db is None:
            return None
        trait_name, trait_value = _retrieval_trait_from_outcome(had_context)
        evidence = f"outcome_id={outcome_id}" if outcome_id is not None else None
        pool = CandidatePool(db)
        await pool.add_observation(
            trait_name, trait_value, "retrieval", evidence=evidence, closed_vocabulary=True,
        )
        return trait_name, trait_value

    async def promote_traits(self) -> list[dict]:
        """Run promotion against the live Store connection (CP-F change 2).

        Before this method existed, `lyra_memory.identity_engine.
        IdentityEngine.consolidate()` — the ONLY code in this repo that ever
        writes to `traits`/`trait_history` — had exactly two callers, and
        the live daemon's turn path was not one of them:

          - `store/passes/dream.py`'s DreamPass.consolidate(), itself only
            reachable through DreamPass.execute(), a ColdPass. Nothing in
            this build ever constructs or runs a ColdPass automatically —
            there is no scheduler for it (grep the tree: the only
            constructors of DreamPass are its own tests and this docstring's
            own review). It exists, is tested, and is simply never invoked
            outside a test.
          - `lyra_memory/__init__.py`'s legacy `MemorySystem`/`DreamingLoop`
            pairing, which does schedule itself (a poll loop against
            DB_PATH/memory.db) — but the live daemon's CognitiveCore is
            constructed with a `lyra_memory.store.Store` (STORE_PATH,
            CP-B), never a MemorySystem. MemorySystem's identity_engine is
            real but attached to a database and a working-memory instance
            the running daemon never opens.

        So "unwired" was not a bug in IdentityEngine (it is fully correct —
        see lyra-memory/tests/test_trait_history.py) and not a missing
        threshold — it is that literally no code path connected the daemon's
        actual per-turn database connection to IdentityEngine at all.
        CP-E already had this exact problem for CandidatePool.add_observation
        and fixed it the same way `consolidate_retrieval_outcome` does:
        construct fresh against `self._memory.db`, the Store connection this
        process actually opened, rather than going through either dead path.
        This method does the same for promotion.

        Called once per turn from runtime.py's `_finish_exchange`, after
        `consolidate_retrieval_outcome` (the daemon's only source of
        candidate evidence today) — "after consolidation" per CHANGES item
        2. Returns IdentityEngine.consolidate()'s own return value: a list
        of dicts, one per candidate that crossed into `traits` for the
        first time this call (empty when nothing crossed, e.g. this turn's
        consolidation only added evidence to a candidate already promoted,
        or memory has no `.db`).

        Read-only exposure back to Lyra deliberately does NOT go through a
        new method here. OUT OF SCOPE forbids "changing retrieval", and
        `lyra_memory/store/context.py` already has a `traits` context block
        (`_traits_block`, budget `CONTEXT_BUDGETS["traits"]`) that SELECTs
        `traits WHERE confidence >= TRAIT_CONFIDENCE_FLOOR` and folds the
        result into the same `context.text` the facts/commitments/recall
        blocks use — wired into the live daemon already via
        `CognitiveCore.retrieve_context` -> `TurnHandler._compose_system_
        prompt`, unchanged by CP-F. The first promoted trait therefore
        becomes visible to her on the next turn that retrieves, through the
        exact mechanism the codebase already built for it, without this
        checkpoint touching retrieval at all. One measured consequence
        worth recording (see DECISIONS.md, CP-F change 2): confidence is
        `evidence_count / TRAIT_THRESHOLDS["core"]` (= evidence/50), so a
        surface-tier trait (evidence_count 5-14) has confidence 0.10-0.28 —
        always below TRAIT_CONFIDENCE_FLOOR (0.3) — and is promoted
        (exists in `traits`) without yet being visible to her; visibility
        starts at evidence_count=15 (character tier, confidence exactly
        0.30).

        No path from her own output reaches this method or anything it
        calls: `runtime.py`'s only inspection of her response is
        `parse_tool_call`/`_TOOL_TOKENS`, a fixed two-entry vision-only
        dict; `promote_traits` is invoked by `_finish_exchange` itself,
        never by anything keyed off her text. See DECISIONS.md for the
        grep that confirms `traits`/`trait_history` are written nowhere
        else in the tree.
        """
        db = getattr(self._memory, "db", None)
        if db is None:
            return []
        from lyra_memory.identity_engine import IdentityEngine
        pool = CandidatePool(db)
        return await IdentityEngine(db, pool).consolidate()

    async def retrieve_repo_context(self, query: str) -> RepoContext:
        """Read-only lookup over indexed repo commits (CP-G change 4) — the
        repo-query analogue of retrieve_context(), but a plain SQL scan over
        `facts WHERE source_kind = 'repo_commit'`, not build_context()
        (OUT OF SCOPE forbids touching retrieval, and this is deliberately
        a separate, much simpler mechanism, not a second recall system).

        Two passes, in order:
          1. Any hex-looking token in `query` (7-40 hex chars) is tried as a
             commit-hash PREFIX first — "what does commit abc1234 do" is a
             stronger, better-defined request than a keyword search.
          2. If fewer than _REPO_QUERY_ROW_LIMIT rows were found this way,
             fill the remainder with a plain substring OR-search over the
             stored commit lines using the query's own words (short/
             stopword-free tokens only) — no FTS index (a new virtual table
             would be a schema change, forbidden); a LIKE scan is adequate
             at the scale of one repository's commit metadata.

        Returns an EMPTY RepoContext (never None) when memory has no `.db`
        or nothing matched — change 6 treats "nothing retrieved" as a
        recorded, executed intent, not an absent one; the caller
        (runtime.py) is what decides whether to log/record, not this
        method returning a sentinel.
        """
        db = getattr(self._memory, "db", None)
        if db is None:
            return RepoContext(text="", commit_hashes=[])

        rows: list[tuple[str, str]] = []
        seen: set[str] = set()

        for tok in _HEX_TOKEN_RE.findall((query or "").lower()):
            async with db.execute(
                "SELECT subject, text FROM facts WHERE source_kind = ?"
                " AND subject LIKE ? ORDER BY valid_from DESC LIMIT ?",
                (REPO_COMMIT_SOURCE_KIND, tok + "%", _REPO_QUERY_ROW_LIMIT),
            ) as cur:
                for subject, text in await cur.fetchall():
                    if subject not in seen:
                        rows.append((subject, text))
                        seen.add(subject)

        if len(rows) < _REPO_QUERY_ROW_LIMIT:
            tokens = [
                t.lower() for t in _REPO_WORD_RE.findall(query or "")
                if len(t) > 2
            ]
            if tokens:
                clauses = " OR ".join("text LIKE ?" for _ in tokens)
                params = [f"%{t}%" for t in tokens]
                async with db.execute(
                    f"SELECT subject, text FROM facts WHERE source_kind = ?"
                    f" AND ({clauses}) ORDER BY valid_from DESC LIMIT ?",
                    (REPO_COMMIT_SOURCE_KIND, *params, _REPO_QUERY_ROW_LIMIT),
                ) as cur:
                    for subject, text in await cur.fetchall():
                        if subject not in seen and len(rows) < _REPO_QUERY_ROW_LIMIT:
                            rows.append((subject, text))
                            seen.add(subject)

        if not rows:
            return RepoContext(text="", commit_hashes=[])

        lines = [f"- {text}" for _subject, text in rows]
        heading = (
            "## Repository history (git log metadata for this repository — "
            "not something you experienced, and not conversation. Cite the "
            "bracketed hash, e.g. [abc1234], for any commit you rely on. Do "
            "not cite a hash you have not seen here.)"
        )
        return RepoContext(
            text=heading + "\n" + "\n".join(lines),
            commit_hashes=[subject for subject, _text in rows],
        )

    async def check_repo_citations(self, reply_text: str) -> tuple[int, int]:
        """Change 5's outcome bit: parse every `[hash]` citation out of
        `reply_text` and check each against the FULL indexed set (a prefix
        LIKE against `facts.subject`, not just the rows this turn happened
        to retrieve — a reply may correctly cite a commit from an earlier
        turn's context or from her own prior knowledge of this repo, and
        that is still a real hit). Returns (hit_count, miss_count), counted
        per citation occurrence, not deduplicated — a hash cited twice is
        two citations to check, not one.

        Deliberately does not suppress, retry, or repair anything (change
        5's own words) — a miss is simply counted.
        """
        db = getattr(self._memory, "db", None)
        hashes = _CITATION_RE.findall(reply_text or "")
        if db is None or not hashes:
            return (0, 0)

        hit = miss = 0
        for h in hashes:
            async with db.execute(
                "SELECT 1 FROM facts WHERE source_kind = ? AND subject LIKE ? LIMIT 1",
                (REPO_COMMIT_SOURCE_KIND, h.lower() + "%"),
            ) as cur:
                row = await cur.fetchone()
            if row is not None:
                hit += 1
            else:
                miss += 1
        return (hit, miss)

    async def record_repo_query_outcome(
        self, user_atom_id: int, context_log_id: int | None, hit_count: int, miss_count: int,
    ) -> int | None:
        """One outcomes row per executed repo_query intent (change 5/6) —
        always recorded when the intent executed, including a (0, 0) turn
        (change 6: zero citations is a recorded outcome, not an absent
        row). `environment` packs hit/miss counts the same free-text
        `key=value;...` way record_retrieval_outcome's `environment` already
        does (OUT OF SCOPE forbids a schema change to give them real
        columns) — `kind=repo_query` at the front lets report.py select
        just these rows out of the shared `outcomes` table without a new
        column to filter on.

        valence is the FALSE/not-FALSE bit change 5 asks for: 0.0 the
        moment miss_count > 0 (a hash cited that is not in the index is a
        false outcome, full stop — one miss makes the whole outcome
        false), 1.0 otherwise (every citation checked out, including the
        zero-citations case — there was nothing to be wrong about).
        """
        record_outcome = getattr(self._memory, "record_outcome", None)
        if record_outcome is None:
            return None
        valence = 0.0 if miss_count > 0 else 1.0
        return await record_outcome(
            intent_atom_id=user_atom_id,
            valence=valence,
            actual="citations_invalid" if miss_count > 0 else "citations_valid",
            predicted="citations_valid",
            environment=(
                f"kind=repo_query;context_log_id={context_log_id};"
                f"hits={hit_count};misses={miss_count}"
            ),
        )

    async def record_repo_query_loss(
        self, user_atom_id: int, context_log_id: int | None, pressure: float, affect_valence: float,
    ) -> int | None:
        """CP-I change 3: a repo_query candidate that LOST to frustration
        inside ActionSelector.select() — pressure was proposed (nonzero)
        but the intent did not appear in tick()'s output — gets an
        `outcomes` row too, not just a log line. Without this, CP-G's
        undeclinable repo_query had no way to lose at all; now that it can,
        a silent drop would make "the loss is visible in the outcome
        table" (DONE-WHEN) false.

        `pressure` and `affect_valence` are what runtime.py already has in
        hand (the constant tick() proposed the candidate at, and
        introspect().valence) — "the losing score" recorded as those two
        numbers, not a re-derivation of ActionSelector's own frustration
        arithmetic (its `effective_weight`, adjustable by bias, is private
        to the selector and was not exposed for this — see interface.py's
        `_last_repo_query_pressure` comment for why that restraint was
        chosen over widening ActionSelector's return contract).

        valence (the outcomes-table column, distinct from affect_valence)
        is 0.0: the candidate never executed — there was nothing to check
        for truth or falsity, only a bid that did not win. `actual="lost"`
        distinguishes this row from a citations_valid/citations_invalid
        win at a glance.
        """
        record_outcome = getattr(self._memory, "record_outcome", None)
        if record_outcome is None:
            return None
        return await record_outcome(
            intent_atom_id=user_atom_id,
            valence=0.0,
            actual="lost",
            predicted="repo_query",
            environment=(
                f"kind=repo_query_loss;context_log_id={context_log_id};"
                f"pressure={pressure};affect_valence={affect_valence}"
            ),
        )

    async def consolidate_repo_citation_outcome(
        self, had_repo_context: bool, hit_count: int, miss_count: int,
        outcome_id: int | None = None,
    ) -> tuple[str, str] | None:
        """CP-H: the citation outcome consolidates (change 2) — reuses
        CandidatePool exactly as consolidate_retrieval_outcome() already
        does for retrieval, not a second consolidation path. Same
        closed_vocabulary=True reasoning as that method and as
        OutcomeConsolidator's frustration vocabulary before it:
        `_repo_citation_trait_from_outcome` is a fixed, four-branch,
        template-generated vocabulary (change 1) — descriptions of
        adjacent branches ("cited, all verified" vs "cited, one false")
        share almost every word except the fact that decides them, exactly
        the shape CP-E measured as unsafe to dedup by embedding. Dedup by
        exact label match instead.

        Provenance (change 3): `evidence` is `outcome_id=<id>` when the
        caller has one — the identical mechanism CP-E built and change 4's
        retrieval consolidator already reuses — so a merged citation
        candidate's `evidence_text` accumulates one line per contributing
        `outcomes` row, parseable back to the exact turns (and their hit/
        miss counts, via that outcome row's own `environment`) that
        produced it. No new provenance code was written for this
        checkpoint; this call site is the whole change.

        Promotion is untouched (change 4): this method never calls
        IdentityEngine, never checks confidence, never special-cases a
        threshold. `CognitiveCore.promote_traits()` (CP-F) already scans
        every row in `candidates` on each call, regardless of category —
        a citation candidate crosses into `traits` through the exact same
        `promote_traits()` call site `runtime.py` already makes after
        retrieval consolidation, no new call, no new condition.

        Returns (trait_name, trait_value) on every call — CandidatePool
        inserts a new row on first sight of a branch and strengthens the
        existing one on every repeat, never a fresh row for something
        already seen. Returns None only when memory has no `.db`.
        """
        db = getattr(self._memory, "db", None)
        if db is None:
            return None
        trait_name, trait_value = _repo_citation_trait_from_outcome(
            had_repo_context, hit_count, miss_count)
        evidence = f"outcome_id={outcome_id}" if outcome_id is not None else None
        pool = CandidatePool(db)
        await pool.add_observation(
            trait_name, trait_value, "repo_citation", evidence=evidence, closed_vocabulary=True,
        )
        return trait_name, trait_value

    async def _ingest_outcome(self, obs: Observation) -> None:
        if obs.predicted is None or obs.actual is None:
            return

        from lyra_core.development import Outcome

        success = obs.predicted == obs.actual
        self._competence.observe_error(0.0 if success else 1.0)

        if not success:
            self._relational.observe_recurrence(_FAILURE_PROBLEM_ID)

        outcome = Outcome(
            intent=Intent(kind=IntentKind.noop, payload={}),
            success=success,
            affect=self._affect.state,
            drive="boredom" if success else "relational",
        )
        if getattr(self._memory, "candidate_pool", None) is not None:
            await self._consolidator.record_outcome(outcome)

    async def _get_promoted_traits(self) -> list:
        identity_engine = getattr(self._memory, "identity_engine", None)
        if identity_engine is None:
            return []
        return await identity_engine.get_top_traits()

    # ── Tick ─────────────────────────────────────────────────────────────────

    def _gate_intents(self, intents: list[Intent]) -> list[Intent]:
        """Run intents through the harm gate; drop blocked ones and log each block.

        This is the single chokepoint all intents must pass before leaving the
        core.  Action-selection produces intents that flow into tick() and
        therefore through here automatically — nothing bypasses it.  A blocked
        intent must never vanish silently; the log line is the trace.
        """
        allowed = []
        for intent in intents:
            decision = self._gate.check(intent)
            if decision.allowed:
                allowed.append(intent)
            else:
                print(
                    f"[{datetime.now().isoformat()}] [gate] BLOCKED"
                    f" {intent.kind}: {decision.reason}"
                )
        return allowed

    async def tick(self, observations: list[Observation], dt: float = 0.1) -> tuple[list[Intent], AffectState]:
        """Ingest observations, advance affect and drives, return (intents, affect).

        a. Sensory observations from most sources persist one atom each
           (_ingest_sensory -> append_atom); "conversation" and "lyra"
           sourced text does not — see _ingest_sensory and ingest_exchange
           (CP-D.0). action_outcome observations with predicted/actual feed
           CompetenceTracker, RelationalDrive (on failure), and the
           OutcomeConsolidator — all using the affect-at-the-time of this
           tick (before this tick's affect update).
        b. Drives advance by dt; "engaged" means at least one observation
           arrived this tick.
        c. AffectEngine advances by dt using the summed drive AffectPushes.
        d. Promoted traits bias ActionSelector; intents pass through the gate.
        """
        from lyra_core.development import bias_from_traits

        for obs in observations:
            if obs.kind == ObservationKind.sensory:
                await self._ingest_sensory(obs)
            elif obs.kind == ObservationKind.action_outcome:
                await self._ingest_outcome(obs)

        engaged = len(observations) > 0
        self._boredom.update(dt, engaged=engaged)
        self._relational.update(dt)

        boredom_push = self._boredom.affect_push
        relational_push = self._relational.affect_push
        self._affect.update(
            dt,
            valence_input=boredom_push.valence_delta + relational_push.valence_delta,
            arousal_input=boredom_push.arousal_delta + relational_push.arousal_delta,
        )

        traits = await self._get_promoted_traits()
        bias = bias_from_traits(traits)

        # CP-D: retrieval is proposed — at full strength, subject to the same
        # frustration flip every other drive-produced intent goes through —
        # exactly when a "conversation" observation arrived this tick (the
        # first tick of an exchange, before retrieve_context() would run).
        # Not "engaged" in general: the later "lyra" tick and any vision tick
        # must not each propose their own retrieval for the same exchange.
        awaiting_reply = any(
            obs.kind == ObservationKind.sensory and obs.source == "conversation"
            for obs in observations
        )
        # CP-I: repo_query is now a scored candidate, not a direct append —
        # `_last_repo_query_pressure` is read by runtime.py (the same
        # already-established pattern it uses for `_boredom`/`_relational`
        # .pressure: "CognitiveCore.tick() returns only (intents, affect) —
        # interface.py is outside [that] checkpoint's closed file set, so
        # drive pressure is read off the core's own drive objects rather
        # than adding a new accessor there", CP-A.1) so it can tell a WIN
        # (the intent appears below) from a LOSS (pressure was nonzero, the
        # intent does not) from NOT-A-CANDIDATE (pressure was zero) without
        # re-deriving ActionSelector's frustration arithmetic itself — the
        # same restraint CP-D's own retrieval-decline log line already
        # showed ("valence is logged as the legible reason, not a
        # re-derivation of the flip's arithmetic").
        self._last_repo_query_pressure = (
            _REPO_QUERY_PRESSURE if (awaiting_reply and _looks_like_repo_query(observations)) else 0.0
        )
        pressures = {
            "boredom": self._boredom.pressure,
            "relational": self._relational.pressure,
            "retrieval": _RETRIEVAL_PRESSURE if awaiting_reply else 0.0,
            "repo_query": self._last_repo_query_pressure,
        }
        intents = self._selector.select(pressures, self._affect.state, bias=bias)
        intents = self._gate_intents(intents)

        return intents, self._affect.state

    def introspect(self) -> AffectState:
        """Read current affect WITHOUT advancing the core (read-only port).

        Deliberately separate from tick() so that observing the box never
        mutates it. This is the port the telemetry/dashboard and Lyra's own
        self-reading use.
        """
        return self._affect.state
