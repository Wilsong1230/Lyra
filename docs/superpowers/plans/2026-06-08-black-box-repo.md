# black-box Repo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `lyra_ai` and `lyra-memory` into a new unified git repo at `/Users/wilsongomez/Developer/Lyra/black-box/`, clean up dead folders in `lyra_ai`, and push to a new GitHub repo named `black-box`.

**Architecture:** Single monorepo root with two service subdirs (`lyra_ai/`, `lyra-memory/`), each keeping their existing internal structure and `pyproject.toml`. Their individual `.git` dirs are stripped; the new root owns git. Old GitHub repos (`Lyra_ai`, `lyra-memory`) remain as archives.

**Tech Stack:** git, GitHub CLI (`gh`), bash, Python (no code changes — filesystem and git operations only)

---

### Task 1: Clean up empty dirs in lyra_ai

These subdirs under `lyra_ai/lyra/core/` contain only `__pycache__` pyc files and `.DS_Store` — no source `.py` files exist. Deleting them first keeps the move clean.

**Files:**
- Delete: `lyra_ai/lyra/core/embodiment/`
- Delete: `lyra_ai/lyra/core/execution/` (including `handlers/`)
- Delete: `lyra_ai/lyra/core/heuristics/` (including `data/`)
- Delete: `lyra_ai/lyra/core/planning/`
- Delete: `lyra_ai/lyra/core/routing/`
- Delete: `lyra_ai/lyra/core/runtime_support/`
- Delete: `lyra_ai/lyra/core/strategy/`
- Delete: `lyra_ai/lyra/core/` (empty after above)
- Delete: `lyra_ai/docs/` (only `.DS_Store`)

- [ ] **Step 1: Verify these dirs have no source files before deleting**

```bash
find /Users/wilsongomez/Developer/Lyra/lyra_ai/lyra/core -type f ! -path '*/__pycache__/*' ! -name '.DS_Store'
```

Expected output: nothing (empty — confirms safe to delete)

- [ ] **Step 2: Delete empty core subdirs and docs**

```bash
rm -rf /Users/wilsongomez/Developer/Lyra/lyra_ai/lyra/core
rm -rf /Users/wilsongomez/Developer/Lyra/lyra_ai/docs
```

- [ ] **Step 3: Verify lyra_ai still has its source files intact**

```bash
find /Users/wilsongomez/Developer/Lyra/lyra_ai/lyra -type f -name '*.py' | sort
```

Expected output:
```
/Users/wilsongomez/Developer/Lyra/lyra_ai/lyra/__init__.py
/Users/wilsongomez/Developer/Lyra/lyra_ai/lyra/assistant.py
/Users/wilsongomez/Developer/Lyra/lyra_ai/lyra/backends.py
/Users/wilsongomez/Developer/Lyra/lyra_ai/lyra/cli.py
/Users/wilsongomez/Developer/Lyra/lyra_ai/lyra/memory.py
/Users/wilsongomez/Developer/Lyra/lyra_ai/lyra/memory_bridge.py
```

- [ ] **Step 4: Commit this cleanup inside lyra_ai's existing git**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra_ai
git add -A
git commit -m "chore: remove empty core subdirs and empty docs folder"
```

---

### Task 2: Create the black-box directory and initialize git

**Files:**
- Create: `/Users/wilsongomez/Developer/Lyra/black-box/` (directory)
- Create: `/Users/wilsongomez/Developer/Lyra/black-box/.gitignore`
- Create: `/Users/wilsongomez/Developer/Lyra/black-box/README.md`

- [ ] **Step 1: Create the directory and initialize git**

```bash
mkdir /Users/wilsongomez/Developer/Lyra/black-box
git init /Users/wilsongomez/Developer/Lyra/black-box
```

Expected output:
```
Initialized empty Git repository in /Users/wilsongomez/Developer/Lyra/black-box/.git/
```

- [ ] **Step 2: Create root .gitignore**

Create the file `/Users/wilsongomez/Developer/Lyra/black-box/.gitignore` with this exact content:

```
# Python
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/

# Virtual environments
venv/
.venv/

# Environment
.env

# macOS
.DS_Store

# Editor
.vscode/
.idea/
```

- [ ] **Step 3: Create root README.md**

Create the file `/Users/wilsongomez/Developer/Lyra/black-box/README.md` with this exact content:

```markdown
# black-box

Core AI services for Lyra.

## Services

| Directory | Purpose |
|---|---|
| `lyra_ai/` | Core CLI assistant — talks to all Lyra services |
| `lyra-memory/` | Four-layer memory system (working, episodic, structured state, identity) |

## Setup

```bash
# Install lyra-memory first (lyra_ai depends on it)
cd lyra-memory
python3 -m venv venv && source venv/bin/activate
pip install -e .

# Install lyra_ai
cd ../lyra_ai
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run
lyra
```

## Environment Variables

| Variable | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | lyra_ai |
| `OPENROUTER_API_KEY` | lyra_ai, lyra-memory (dreaming loop) |
| `CEREBRAS_API_KEY` | lyra_ai |
| `EMBODIMENT_URL` | lyra_ai (default: http://localhost:8000) |
| `VOICE_URL` | lyra_ai (default: http://localhost:8001) |
| `LISTEN_URL` | lyra_ai (default: http://localhost:8002) |
| `VISION_URL` | lyra_ai (default: http://localhost:8003) |
```

- [ ] **Step 4: Verify the directory looks right**

```bash
ls -la /Users/wilsongomez/Developer/Lyra/black-box/
```

Expected output:
```
.git/
.gitignore
README.md
```

---

### Task 3: Move lyra_ai into black-box

**Files:**
- Move: `/Users/wilsongomez/Developer/Lyra/lyra_ai/` → `/Users/wilsongomez/Developer/Lyra/black-box/lyra_ai/`
- Delete: `/Users/wilsongomez/Developer/Lyra/black-box/lyra_ai/.git/`

- [ ] **Step 1: Move lyra_ai**

```bash
mv /Users/wilsongomez/Developer/Lyra/lyra_ai /Users/wilsongomez/Developer/Lyra/black-box/lyra_ai
```

- [ ] **Step 2: Remove the nested .git dir**

```bash
rm -rf /Users/wilsongomez/Developer/Lyra/black-box/lyra_ai/.git
```

- [ ] **Step 3: Verify the move and that no nested .git remains**

```bash
ls /Users/wilsongomez/Developer/Lyra/black-box/lyra_ai/
ls /Users/wilsongomez/Developer/Lyra/black-box/lyra_ai/.git 2>&1
```

Expected: first command lists `lyra/`, `tests/`, `main.py`, `pyproject.toml`, etc. Second command errors with "No such file or directory".

---

### Task 4: Move lyra-memory into black-box

**Files:**
- Move: `/Users/wilsongomez/Developer/Lyra/lyra-memory/` → `/Users/wilsongomez/Developer/Lyra/black-box/lyra-memory/`
- Delete: `/Users/wilsongomez/Developer/Lyra/black-box/lyra-memory/.git/`

- [ ] **Step 1: Move lyra-memory**

```bash
mv /Users/wilsongomez/Developer/Lyra/lyra-memory /Users/wilsongomez/Developer/Lyra/black-box/lyra-memory
```

- [ ] **Step 2: Remove the nested .git dir**

```bash
rm -rf /Users/wilsongomez/Developer/Lyra/black-box/lyra-memory/.git
```

- [ ] **Step 3: Verify the move and that no nested .git remains**

```bash
ls /Users/wilsongomez/Developer/Lyra/black-box/lyra-memory/
ls /Users/wilsongomez/Developer/Lyra/black-box/lyra-memory/.git 2>&1
```

Expected: first command lists `lyra_memory/`, `tests/`, `pyproject.toml`, `MEMORY_SPEC.md`, etc. Second command errors with "No such file or directory".

---

### Task 5: Initial commit

- [ ] **Step 1: Check what git sees**

```bash
cd /Users/wilsongomez/Developer/Lyra/black-box
git status
```

Expected: many untracked files under `lyra_ai/` and `lyra-memory/`.

- [ ] **Step 2: Stage everything**

```bash
cd /Users/wilsongomez/Developer/Lyra/black-box
git add .gitignore README.md lyra_ai/ lyra-memory/
```

- [ ] **Step 3: Verify staged files look right (no venv or pycache slipping in)**

```bash
cd /Users/wilsongomez/Developer/Lyra/black-box
git status --short | grep -v '^\?' | head -40
```

Confirm you do NOT see `venv/`, `.venv/`, `__pycache__/`, `*.pyc`, `.DS_Store`, or `*.egg-info/` in the staged list. If you do, check that `.gitignore` was written correctly in Task 2 Step 2.

- [ ] **Step 4: Create the initial commit**

```bash
cd /Users/wilsongomez/Developer/Lyra/black-box
git commit -m "$(cat <<'EOF'
feat: initialize black-box repo with lyra_ai and lyra-memory

Merges lyra_ai (core CLI) and lyra-memory (four-layer memory system)
into a single monorepo. Both services keep their own folder and
pyproject.toml. Old GitHub repos remain as archives.
EOF
)"
```

- [ ] **Step 5: Verify commit**

```bash
cd /Users/wilsongomez/Developer/Lyra/black-box
git log --oneline
git show --stat HEAD | tail -20
```

Expected: one commit, shows files from both `lyra_ai/` and `lyra-memory/`.

---

### Task 6: Create GitHub repo and push

Requires GitHub CLI (`gh`). If not installed: `brew install gh` and `gh auth login`.

- [ ] **Step 1: Check gh is authenticated**

```bash
gh auth status
```

Expected: shows logged-in account (`Wilsong1230`).

- [ ] **Step 2: Create the GitHub repo**

```bash
cd /Users/wilsongomez/Developer/Lyra/black-box
gh repo create Wilsong1230/black-box --private --description "Core AI services: lyra_ai CLI + lyra-memory system" --source=. --remote=origin
```

Flag notes:
- `--private`: keeps it private; change to `--public` if you want it public
- `--source=.`: uses current directory as the local repo
- `--remote=origin`: adds the GitHub URL as `origin`

- [ ] **Step 3: Push**

```bash
cd /Users/wilsongomez/Developer/Lyra/black-box
git push -u origin main
```

If your default branch is `master` instead of `main`:
```bash
git push -u origin master
```

- [ ] **Step 4: Verify on GitHub**

```bash
gh repo view Wilsong1230/black-box --web
```

Opens the repo in browser. Confirm both `lyra_ai/` and `lyra-memory/` appear in the file tree.

---

### Task 7: Verify the full setup

- [ ] **Step 1: Confirm final directory structure**

```bash
ls /Users/wilsongomez/Developer/Lyra/black-box/
```

Expected:
```
.git/
.gitignore
README.md
lyra-memory/
lyra_ai/
```

- [ ] **Step 2: Confirm lyra_ai source files are intact**

```bash
find /Users/wilsongomez/Developer/Lyra/black-box/lyra_ai/lyra -name '*.py' | sort
```

Expected — all 6 source files present, no `core/` subdirs:
```
.../lyra/__init__.py
.../lyra/assistant.py
.../lyra/backends.py
.../lyra/cli.py
.../lyra/memory.py
.../lyra/memory_bridge.py
```

- [ ] **Step 3: Confirm lyra-memory source files are intact**

```bash
find /Users/wilsongomez/Developer/Lyra/black-box/lyra-memory/lyra_memory -name '*.py' | sort
```

Expected — all source files present:
```
.../lyra_memory/__init__.py
.../lyra_memory/__main__.py
.../lyra_memory/candidate_pool.py
.../lyra_memory/config.py
.../lyra_memory/db.py
.../lyra_memory/dreaming_loop.py
.../lyra_memory/embeddings.py
.../lyra_memory/identity_engine.py
.../lyra_memory/models.py
.../lyra_memory/retrieval.py
.../lyra_memory/structured_state.py
.../lyra_memory/working_memory.py
```

- [ ] **Step 4: Smoke-test lyra_ai still imports lyra_memory correctly**

```bash
cd /Users/wilsongomez/Developer/Lyra/black-box/lyra-memory
source venv/bin/activate
pip install -e . -q

cd /Users/wilsongomez/Developer/Lyra/black-box/lyra_ai
source .venv/bin/activate
pip install -e ".[dev]" -q
python -c "from lyra.memory_bridge import MemoryBridge; print('OK')"
```

Expected output: `OK`

- [ ] **Step 5: Confirm no nested .git dirs remain**

```bash
find /Users/wilsongomez/Developer/Lyra/black-box -name '.git' -type d
```

Expected — only the root:
```
/Users/wilsongomez/Developer/Lyra/black-box/.git
```
