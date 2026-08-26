# black-box Repo Design

**Date:** 2026-06-08  
**Status:** Approved

## Goal

Merge `lyra_ai` and `lyra-memory` into a single new git repository named `black-box`, located at `/Developer/Lyra/black-box/`. Both services maintain their own subdirectory and internal structure. Start fresh git history; old GitHub repos remain as archives.

---

## Final Directory Structure

```
/Developer/Lyra/
├── black-box/                    ← new repo root
│   ├── lyra_ai/                  ← moved from Lyra/lyra_ai/
│   │   ├── lyra/
│   │   │   ├── assistant.py
│   │   │   ├── backends.py
│   │   │   ├── cli.py
│   │   │   ├── memory.py
│   │   │   └── memory_bridge.py
│   │   ├── tests/
│   │   ├── main.py
│   │   └── pyproject.toml
│   ├── lyra-memory/              ← moved from Lyra/lyra-memory/
│   │   ├── lyra_memory/
│   │   ├── tests/
│   │   └── pyproject.toml
│   ├── .gitignore
│   └── README.md
├── lyra-embodiment/              ← untouched
├── lyra-voice/                   ← untouched
├── lyra-listen/                  ← untouched
├── lyra-vision/                  ← untouched
└── lyra-mcp/                     ← untouched
```

---

## Cleanup: lyra_ai Empty Dirs

These dirs in `lyra_ai/lyra/core/` contain only `__pycache__` pyc files and `.DS_Store` — no source files. Delete before move:

- `lyra/core/embodiment/`
- `lyra/core/execution/` (including `handlers/` subdir)
- `lyra/core/heuristics/` (including `data/` subdir)
- `lyra/core/planning/`
- `lyra/core/routing/`
- `lyra/core/runtime_support/`
- `lyra/core/strategy/`
- `lyra/core/` (empty after above)
- `docs/` (empty, only `.DS_Store`)

---

## Git Setup Steps

1. `mkdir /Developer/Lyra/black-box && git init /Developer/Lyra/black-box`
2. Clean up empty dirs in `lyra_ai`
3. Move `lyra_ai/` into `black-box/lyra_ai/`
4. Move `lyra-memory/` into `black-box/lyra-memory/`
5. Remove nested `.git` dirs (`black-box/lyra_ai/.git`, `black-box/lyra-memory/.git`)
6. Create root `.gitignore` covering: `venv/`, `.venv/`, `__pycache__/`, `*.pyc`, `.DS_Store`, `.env`, `*.egg-info/`, `.pytest_cache/`
7. Create root `README.md`
8. Initial commit: all files
9. Create GitHub repo `black-box` under Wilsong1230 account
10. Push

---

## What Is NOT Changing

- `lyra-embodiment`, `lyra-voice`, `lyra-listen`, `lyra-vision`, `lyra-mcp` stay in `Lyra/` untouched
- Old `Lyra_ai.git` and `lyra-memory.git` GitHub repos remain as archives
- Internal code of `lyra_ai` and `lyra-memory` is unchanged

---

## Constraints

- `lyra_ai` imports `lyra_memory` via `memory_bridge.py` — install both packages from their respective dirs with `pip install -e .`
- `lyra-memory` requires Homebrew or python.org Python (not macOS system Python) due to sqlite-vec extension loading
