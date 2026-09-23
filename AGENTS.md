# Project Instructions

## Project

`harnessAi` is a macOS multi-agent coding harness: a Python engine (`engine/harness.py`, standard library only, Python 3.9+) that orchestrates Codex, OpenCode (Kimi), Claude Code, and Antigravity CLIs with per-role fallback, plus a SwiftUI app (`app/HarnessMonitor.swift`) that drives the engine and visualizes runs.

## Structure

- `engine/harness.py`: CLI + orchestration (snapshot, plan, parallel workers in git worktrees, review, integration, fallback, config).
- `app/HarnessMonitor.swift`: single-file SwiftUI app. Reads `<project>/.ai-harness/runs/*/state.json`, calls the engine through `/bin/zsh -lc`, edits `~/.ai-harness/config.json`. Bundles and bootstraps the engine from `Contents/Resources/harness.py`.
- `scripts/build.sh`: build universal app, optional install and release zip.
- `scripts/install.sh`: install latest GitHub Release.
- `.github/workflows/ci.yml`, `release.yml`: CI build; tag `v*` publishes a Release.

## Commands

- Build + install locally: `scripts/build.sh`
- Build only: `scripts/build.sh --no-install`
- Release zip: `scripts/build.sh --no-install --zip`
- Engine syntax check: `python3 -m py_compile engine/harness.py`
- No automated test suite exists. Verify engine changes with a throwaway Git repo under a temp directory, never with real projects.

## Development Rules

- Engine: standard library only, keep Python 3.9 compatibility (`from __future__ import annotations`, `typing` generics).
- `state.json` is the contract between engine and app; add fields compatibly (app decodes with `convertFromSnakeCase`, optional for new fields).
- Every model call goes through `call_agent` so fallback, stop handling, and logging stay uniform.
- Never run agents against the user's working tree directly; workers use detached worktrees, and only `apply` touches the project.
- Keep protected-path checks (`SENSITIVE_SEGMENTS`, project `protected_paths`) on every owned path and task scope.
- Never commit credentials, run artifacts (`.ai-harness/runs/`), `build/`, or `dist/`.
- The app is ad-hoc signed; do not add signing identities or notarization secrets to the repo.

## Git

- Default branch `main`, remote `https://github.com/HAHEONHWI/harnessAi` (public).
- Branches: `feat/<topic>`, `fix/<topic>`, `docs/<topic>`, `chore/<topic>`.
- Commit messages: `prefix: description` with `feat`, `fix`, `refactor`, `docs`, `style`, `test`, `chore`, `perf`, `ci`, `build`. Keep them short and specific, e.g. `feat: add task path scope`.
- Ask before force-pushing, rewriting history, deleting branches, or creating release tags.

## Release

- Push tag `vX.Y.Z` to publish; `release.yml` builds `dist/AI-Harness-macOS.zip` and creates the GitHub Release. Manual `workflow_dispatch` only uploads an artifact.
