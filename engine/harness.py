#!/usr/bin/env python3
"""AI harness engine.

Flow per run: snapshot project -> coordinator plans -> workers run in parallel
(isolated worktrees) -> optional security review -> coordinator review ->
approved patches integrated into a private integration worktree -> repeat up
to max_rounds -> final.patch the user applies with `apply` -> a summarizer
writes report.md explaining the result.

Every model call goes through a fallback chain; when a provider hits a usage,
quota, or rate limit (or is disabled/missing), the next provider is tried.
Provider on/off and chains live in ~/.ai-harness/config.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

HOME_DIR = Path(os.environ.get("AI_HARNESS_HOME", str(Path.home() / ".ai-harness")))
CONFIG_PATH = HOME_DIR / "config.json"
OPENCODE_AGENTS = Path.home() / ".config/opencode/agents"

DEFAULT_CONFIG: Dict[str, Any] = {
    "providers": {
        "sol": {"label": "Codex Sol", "enabled": True, "model": "gpt-5.6-sol", "effort": "high"},
        "luna": {"label": "Codex Luna", "enabled": True, "model": "gpt-5.6-luna", "effort": "max"},
        "kimi": {"label": "OpenCode Kimi", "enabled": True, "model": "kimi-code-plan-global/kimi-for-coding"},
        "claude": {"label": "Claude Opus", "enabled": True, "model": "opus", "effort": "high", "max_budget_usd": 5},
        "antigravity": {"label": "Antigravity", "enabled": True, "model": ""},
    },
    "fallback": {
        "sol": ["kimi", "claude", "antigravity"],
        "luna": ["kimi", "antigravity", "claude"],
        "kimi": ["luna", "antigravity", "claude"],
        "claude": ["sol", "luna", "kimi", "antigravity"],
        "antigravity": ["kimi", "luna", "claude"],
    },
    "max_rounds": 3,
    "max_workers": 4,
    "call_timeout_minutes": 45,
    "summary_role": "luna",  # writes report.md after a run; "" disables it
}
PROVIDER_CLI = {"sol": "codex", "luna": "codex", "kimi": "opencode", "claude": "claude", "antigravity": "agy"}
WORKER_ROLES = ("luna", "kimi", "antigravity", "claude", "sol")
COORDINATOR = "claude"  # plans and reviews every round

# GUI apps start with a minimal PATH; make the agent CLIs reachable.
os.environ["PATH"] = os.pathsep.join(
    [os.environ.get("PATH", "/usr/bin:/bin"), str(Path.home() / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin"]
)

LIMIT_RE = re.compile(
    r"usage limit|rate[ _-]?limit|quota|credit balance|out of credits|insufficient[ _]credits?|"
    r"resource[ _]exhausted|too many requests|\b429\b|does not have access|not supported when using|"
    r"exceeded|budget|upgrade (your|to|plan)|not logged in|unauthori[sz]ed|authentication",
    re.I,
)
# CLIs that exit 0 without finishing (e.g. agy auto-denying a tool, or hitting its print timeout mid-turn).
NO_OUTPUT_RE = re.compile(r"no output produced|auto-denied|cannot prompt for|print timeout after", re.I)
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
PATH_RE = re.compile(r"^([A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$")
# Never assignable anywhere. Projects add their own via .ai-harness/config.json {"protected_paths": [...]}.
SENSITIVE_SEGMENTS = {".git", ".ai-harness", ".vercel", ".firebase", ".aws", ".ssh", ".gnupg", "node_modules"}
# State directories that agent CLIs/plugins write on their own; never part of a patch.
TOOL_NOISE = (".omo/", ".opencode/node_modules/", ".gemini/", ".antigravity/", ".codex/", ".claude/settings.local.json")
GIT_IDENT = ["-c", "user.name=ai-harness", "-c", "user.email=ai-harness@localhost", "-c", "commit.gpgsign=false"]

COMMON_RULES = """Operating rules:
- Read and follow AGENTS.md / CLAUDE.md in the repository if present.
- Never read or print secrets or private data: .env*, credential/key files, local settings, generated documents, or anything AGENTS.md marks sensitive.
- Do not commit, push, deploy, change git config or remotes, or install dependencies unless the assignment says so.
- Do not add or run tests unless the assignment says so."""


class HarnessError(Exception):
    pass


class Stopped(HarnessError):
    pass


def now() -> float:
    return time.time()


def load_config() -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        user = json.loads(CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        user = {}
    for name, values in user.get("providers", {}).items():
        cfg["providers"].setdefault(name, {}).update(values)
    cfg["fallback"].update(user.get("fallback", {}))
    for key in ("max_rounds", "max_workers", "call_timeout_minutes", "summary_role"):
        if key in user:
            cfg[key] = user[key]
    return cfg


def write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, path)


def git(repo: Path, *args: str, env: Optional[Dict[str, str]] = None, check: bool = True, strip: bool = True) -> str:
    """Run git in repo. Pass strip=False for diffs: trailing whitespace is part of the patch."""
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    if check and proc.returncode != 0:
        raise HarnessError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip() if strip else proc.stdout


def repo_root(path: str) -> Path:
    return Path(git(Path(path).expanduser(), "rev-parse", "--show-toplevel"))


def runs_dir(repo: Path) -> Path:
    return repo / ".ai-harness" / "runs"


def ensure_runs_ignored(repo: Path) -> None:
    probe = ".ai-harness/runs/probe"
    if subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", probe]).returncode == 0:
        return
    exclude = Path(git(repo, "rev-parse", "--git-common-dir"))
    exclude = (exclude if exclude.is_absolute() else repo / exclude) / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a") as fh:
        fh.write("\n/.ai-harness/runs/\n")


def snapshot(repo: Path, run_id: str) -> str:
    """Commit the working tree (tracked + untracked, minus ignored) without touching HEAD/index."""
    fd, index = tempfile.mkstemp(prefix="ai-harness-index-")
    os.close(fd)
    os.unlink(index)
    env = {**os.environ, "GIT_INDEX_FILE": index}
    has_head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True).returncode == 0
    try:
        if has_head:
            git(repo, "read-tree", "HEAD", env=env)
        git(repo, "add", "-A", env=env)
        tree = git(repo, "write-tree", env=env)
    finally:
        if os.path.exists(index):
            os.unlink(index)
    parent = ["-p", "HEAD"] if has_head else []
    sha = git(repo, *GIT_IDENT, "commit-tree", tree, *parent, "-m", f"ai-harness snapshot {run_id}")
    git(repo, "update-ref", f"refs/ai-harness/{run_id}", sha)
    return sha


def protected_paths(repo: Path) -> List[str]:
    try:
        data = json.loads((repo / ".ai-harness" / "config.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return [str(p).strip().strip("/") for p in data.get("protected_paths", []) if str(p).strip().strip("/")]


def is_protected(rel: str, protected: List[str]) -> bool:
    segments = rel.split("/")
    if any(seg in SENSITIVE_SEGMENTS or seg.startswith(".env") for seg in segments):
        return True
    return any(rel == p or rel.startswith(p + "/") or p.startswith(rel + "/") for p in protected)


def extract_json(text: str) -> Dict[str, Any]:
    candidates = re.findall(r"BEGIN_JSON\s*(.*?)\s*END_JSON", text, re.S)
    candidates += re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    for raw in reversed(candidates):
        raw = raw.strip().strip("`")
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    decoder = json.JSONDecoder()
    for match in reversed([m.start() for m in re.finditer(r"\{", text)]):
        try:
            data, _ = decoder.raw_decode(text[match:])
            if isinstance(data, dict) and ("workers" in data or "approve" in data or "done" in data):
                return data
        except json.JSONDecodeError:
            continue
    raise HarnessError("agent did not return a JSON block")


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


# ---------------------------------------------------------------------------
# Run state


class Run:
    def __init__(self, repo: Path, run_id: str):
        self.repo = repo
        self.id = run_id
        self.dir = runs_dir(repo) / run_id
        # Reentrant: the SIGTERM handler runs on the main thread and may interrupt it while it holds the lock.
        self.lock = threading.RLock()
        self.data: Dict[str, Any] = json.loads((self.dir / "state.json").read_text())
        self.exhausted: set = set()
        self.children: set = set()
        self.stopping = False

    @classmethod
    def create(cls, repo: Path, command: str, slug: str, rounds: int, scope: List[str]) -> "Run":
        run_id = f"{dt.datetime.now():%Y%m%d-%H%M%S}-{slug}"
        run_dir = runs_dir(repo) / run_id
        for sub in ("logs", "prompts", "results", "worktrees"):
            (run_dir / sub).mkdir(parents=True, exist_ok=False)
        write_json(run_dir / "state.json", {
            "id": run_id, "repo": str(repo), "command": command, "scope": scope, "status": "queued",
            "round": 0, "max_rounds": rounds, "pid": None, "started": now(), "updated": now(),
            "ended": None, "base": None, "error": None, "summary": None, "report": None, "feedback": None,
            "rounds": [], "tasks": [], "events": [],
        })
        return cls(repo, run_id)

    def save(self) -> None:
        self.data["updated"] = now()
        write_json(self.dir / "state.json", self.data)

    def update(self, **fields: Any) -> None:
        with self.lock:
            self.data.update(fields)
            self.save()

    def event(self, message: str) -> None:
        with self.lock:
            self.data["events"].append({"t": now(), "msg": message})
            self.save()
        print(f"[{dt.datetime.now():%H:%M:%S}] {message}", flush=True)

    def add_task(self, **fields: Any) -> Dict[str, Any]:
        task = {"state": "pending", "provider": None, "started": None, "ended": None, "attempts": [],
                "log": None, "result": None, "patch": None, "changed_files": None, **fields}
        with self.lock:
            self.data["tasks"].append(task)
            self.save()
        return task

    def set_task(self, task: Dict[str, Any], **fields: Any) -> None:
        with self.lock:
            task.update(fields)
            self.save()

    def check_stop(self) -> None:
        if self.stopping:
            raise Stopped("stopped by user")


# ---------------------------------------------------------------------------
# Providers and fallback


def provider_command(provider: str, cfg: Dict[str, Any], mode: str, workdir: Path, prompt: str,
                     message_file: Path, timeout_min: int) -> Tuple[List[str], Optional[str]]:
    write = mode == "write"
    model = cfg.get("model") or ""
    if provider in ("sol", "luna"):
        cmd = ["codex", "exec", "--model", model, "-c", 'approval_policy="never"',
               "-c", f'model_reasoning_effort="{cfg.get("effort", "high")}"',
               "--sandbox", "workspace-write" if write else "read-only", "--ephemeral",
               "--skip-git-repo-check", "-C", str(workdir), "-o", str(message_file), "-"]
        return cmd, prompt
    if provider == "kimi":
        agent = "harness-kimi-writer" if write else "harness-kimi-reader"
        return ["opencode", "run", "--agent", agent, "--model", model, "--dir", str(workdir), prompt], None
    if provider == "claude":
        tools = "Read,Glob,Grep,Edit,Write" if write else "Read,Glob,Grep"
        cmd = ["claude", "-p", prompt, "--model", model or "opus", "--effort", cfg.get("effort", "max"),
               "--output-format", "stream-json", "--verbose",
               "--permission-mode", "acceptEdits" if write else "plan", "--allowedTools", tools,
               "--no-session-persistence", "--max-budget-usd", str(cfg.get("max_budget_usd", 5)),
               "--append-system-prompt", COMMON_RULES]
        return cmd, None
    if provider == "antigravity":
        cmd = ["agy", "-p", prompt, "--mode", "accept-edits" if write else "plan",
               "--print-timeout", f"{timeout_min}m"]
        if model:
            cmd += ["--model", model]
        # Headless agy auto-denies shell commands it cannot prompt for. "skip_permissions" auto-approves
        # every tool instead; it runs without --sandbox because sandboxed commands hang until the print timeout.
        if cfg.get("skip_permissions"):
            cmd.append("--dangerously-skip-permissions")
        elif write:
            cmd.append("--sandbox")
        return cmd, None
    raise HarnessError(f"unknown provider {provider}")


def classify_failure(code: int, output: str) -> str:
    tail = "\n".join(output.strip().splitlines()[-40:])
    if code == 0 and NO_OUTPUT_RE.search(tail):
        return "no-output"
    if code != 0 and LIMIT_RE.search(tail):
        return "limit"
    if code == 0 and re.search(r"(?im)^\W*(error|fatal)\b.*$", tail):
        lines = [l for l in tail.splitlines() if re.match(r"(?i)^\W*(error|fatal)\b", l)]
        if any(LIMIT_RE.search(l) for l in lines):
            return "limit"
    return "ok" if code == 0 else "error"


def call_agent(run: Run, task: Dict[str, Any], role: str, mode: str, workdir: Path, prompt: str) -> str:
    base_name = task["name"]
    (run.dir / "prompts" / f"{base_name}.md").write_text(prompt)
    run.set_task(task, prompt=f"prompts/{base_name}.md", state="running", started=now())
    chain = [role] + list(load_config()["fallback"].get(role, []))
    seen: List[str] = []
    for provider in chain:
        run.check_stop()
        if provider in seen:
            continue
        seen.append(provider)
        cfg = load_config()  # re-read so on/off toggles apply mid-run
        pcfg = cfg["providers"].get(provider)
        skip = None
        if not pcfg or not pcfg.get("enabled", False):
            skip = "disabled"
        elif provider in run.exhausted:
            skip = "limit or no output earlier in this run"
        elif not shutil.which(PROVIDER_CLI[provider]):
            skip = f"{PROVIDER_CLI[provider]} not installed"
        if skip:
            run.set_task(task, attempts=task["attempts"] + [{"provider": provider, "status": "skipped", "reason": skip}])
            continue

        log_rel = f"logs/{base_name}.{provider}.log"
        message_file = run.dir / "results" / f"{base_name}.{provider}.message.md"
        timeout_min = int(cfg.get("call_timeout_minutes", 45))
        cmd, stdin_text = provider_command(provider, pcfg, mode, workdir, prompt, message_file, timeout_min)
        run.set_task(task, provider=provider, log=log_rel)
        run.event(f"{base_name}: {pcfg.get('label', provider)} started")
        stream = ClaudeStream() if provider == "claude" else None
        code = run_process(run, cmd, stdin_text, workdir, run.dir / log_rel, timeout_min * 60,
                           stream.line if stream else None)
        run.check_stop()  # some CLIs exit 0 when killed; never treat a stopped call as success
        if stream and stream.result is not None:
            message_file.write_text(stream.result)
        output = ANSI_RE.sub("", (run.dir / log_rel).read_text(errors="replace"))
        status = "timeout" if code == 124 else classify_failure(code, output)
        result = ""
        if status == "ok":
            result = message_file.read_text(errors="replace") if message_file.exists() else output
            if not result.strip():
                status = "no-output"
        attempt = {"provider": provider, "status": status, "exit": code, "log": log_rel}
        run.set_task(task, attempts=task["attempts"] + [attempt])
        if status == "ok":
            result_rel = f"results/{base_name}.result.md"
            (run.dir / result_rel).write_text(result)
            run.set_task(task, result=result_rel)
            return result
        if status in ("limit", "no-output"):
            run.exhausted.add(provider)
            reason = "hit a usage/rate limit" if status == "limit" else "finished without producing output"
            run.event(f"{base_name}: {provider} {reason}, falling back")
            continue
        raise HarnessError(f"{base_name}: {provider} failed ({status}, exit {code}); see {log_rel}")
    raise HarnessError(f"{base_name}: no available provider in chain {' -> '.join(chain)}")


def run_process(run: Run, cmd: List[str], stdin_text: Optional[str], cwd: Path, log: Path, timeout: int,
                formatter: Optional[Callable[[str], Optional[str]]] = None) -> int:
    """Run cmd with output in log. With a formatter, raw output goes to <log>.jsonl and
    the log gets the formatter's readable lines as they stream in."""
    with log.open("w") as out:
        proc = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE if formatter else out, stderr=subprocess.STDOUT, text=True,
                                errors="replace", start_new_session=True)
        reader = None
        if formatter:
            def pump() -> None:
                with log.with_suffix(".jsonl").open("w") as raw:
                    for line in proc.stdout:
                        raw.write(line)
                        text = formatter(line)
                        if text:
                            out.write(text + "\n")
                            out.flush()
            reader = threading.Thread(target=pump, daemon=True)
            reader.start()
        with run.lock:
            run.children.add(proc)
        try:
            if stdin_text is not None:
                proc.stdin.write(stdin_text)
                proc.stdin.close()
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_group(proc)
            return 124
        finally:
            if reader:
                reader.join(timeout=10)
            with run.lock:
                run.children.discard(proc)


class ClaudeStream:
    """Turns `claude -p --output-format stream-json` events into readable progress lines
    and keeps the final result text."""

    def __init__(self) -> None:
        self.result: Optional[str] = None

    def line(self, raw: str) -> Optional[str]:
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            return raw.rstrip("\n") or None  # stderr and other plain text pass through
        if not isinstance(ev, dict):
            return None
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            return f"[start] model {ev.get('model', '?')}"
        if kind == "assistant":
            parts = []
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    parts.append(clip(block["text"].strip(), 400))
                elif block.get("type") == "tool_use":
                    args = block.get("input") or {}
                    target = next((str(args[k]) for k in ("file_path", "path", "pattern", "command") if k in args), "")
                    parts.append(f"[tool] {block.get('name')} {target}".rstrip())
            return "\n".join(parts) or None
        if kind == "result":
            self.result = str(ev.get("result") or "")
            head = "[done]"
            if ev.get("is_error"):  # keep subtype/errors/status so classify_failure can spot limits (e.g. error_max_budget_usd, 429)
                details = [str(ev.get("subtype") or ""), *map(str, ev.get("errors") or [])]
                if ev.get("api_error_status"):
                    details.append(f"status {ev['api_error_status']}")
                head = "[error] " + " ".join(d for d in details if d)
            cost = ev.get("total_cost_usd")
            meta = f" {ev.get('num_turns', '?')} turns" + (f", ${cost:.2f}" if isinstance(cost, (int, float)) else "")
            return f"{head}{meta}\n{self.result}"
        return None


def kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


# ---------------------------------------------------------------------------
# Prompts


def role_guide(cfg: Dict[str, Any]) -> str:
    notes = {
        "luna": "Codex Luna: fast, medium cost. Bounded implementation and QA.",
        "kimi": "OpenCode Kimi: cheap but slower. Broad mapping, repetitive edits, docs.",
        "antigravity": "Google Antigravity: frontend/UI work and alternative implementations.",
        "claude": "Claude Opus: coordinator and security reviewer; avoid assigning worker tasks to it.",
        "sol": "Codex Sol: strongest Codex, expensive. Hard design or security-critical code.",
    }
    lines = []
    for role in WORKER_ROLES:
        state = "on" if cfg["providers"].get(role, {}).get("enabled") else "off (auto-replaced by fallback)"
        lines.append(f"- {role} [{state}]: {notes[role]}")
    return "\n".join(lines)


def plan_prompt(run: Run, round_no: int, feedback: Optional[str], error: Optional[str]) -> str:
    cfg = load_config()
    history = ""
    if feedback:
        history = f"\nPrevious round review feedback (address it now):\n{feedback}\n"
    retry = f"\nYour previous plan was rejected by the harness: {error}\nFix it.\n" if error else ""
    protected = protected_paths(run.repo)
    protected_text = f" ({', '.join(protected)})" if protected else ""
    scope = run.data.get("scope") or []
    scope_text = ("\nTask scope: only these files/folders may be changed; every owned path must be one of them "
                  "or inside them:\n" + "\n".join(f"- {p}" for p in scope) + "\n") if scope else ""
    return f"""You are the coordinator of a multi-agent coding harness. Round {round_no} of {run.data['max_rounds']}.
Earlier rounds' approved changes are already present in this working tree.

User command:
{run.data['command']}
{scope_text}{history}{retry}
Worker roles:
{role_guide(cfg)}

Inspect the repository read-only, then split the remaining work into 1-{cfg['max_workers']} independent assignments that can run in parallel for speed.
Each assignment owns disjoint paths (files or directories relative to the repo root); workers may only modify their owned paths.
Never assign .git, .env*, secrets, generated output, the .ai-harness directory, or protected paths{protected_text}.
Set "security_review": true when the change touches auth, privacy, access rules, secrets, payments, or deployment.
If the work is already complete, return {{"done": true, "summary": "..."}} with no workers.

{COMMON_RULES}

Reply with the plan only, between BEGIN_JSON and END_JSON:
BEGIN_JSON
{{"done": false, "goal": "one outcome", "acceptance": ["observable behavior"], "security_review": false,
  "workers": [{{"name": "api", "role": "luna", "owned_paths": ["app/reports.py"], "assignment": "precise instructions"}}]}}
END_JSON
"""


def worker_prompt(plan: Dict[str, Any], worker: Dict[str, Any]) -> str:
    acceptance = "\n".join(f"- {a}" for a in plan.get("acceptance", []))
    return f"""You are a worker in a multi-agent coding harness.

Goal: {plan.get('goal', '')}
Acceptance criteria:
{acceptance}

Your assignment ({worker['name']}):
{worker['assignment']}

OWNED_PATHS (modify only these; other agents own everything else): {', '.join(worker['owned_paths'])}

{COMMON_RULES}

When finished, report changed files, assumptions, and the checks you actually performed."""


def patch_digest(run: Run, workers: List[Dict[str, Any]], limit: int = 60000) -> str:
    parts = []
    per = max(4000, limit // max(1, len(workers)))
    for task in workers:
        patch = (run.dir / task["patch"]).read_text(errors="replace") if task.get("patch") else ""
        result = (run.dir / task["result"]).read_text(errors="replace") if task.get("result") else ""
        parts.append(
            f"### {task['name']} (role {task['role']}, ran on {task.get('provider')}, state {task['state']})\n"
            f"Owned: {', '.join(task.get('owned_paths', []))}\n"
            f"Worker report:\n{clip(result, 3000)}\n\nPatch:\n```diff\n{clip(patch, per) or '(no changes)'}\n```"
        )
    return "\n\n".join(parts)


def security_prompt(run: Run, plan: Dict[str, Any], workers: List[Dict[str, Any]]) -> str:
    return f"""Review these worker patches for security, privacy, and correctness risks before integration.
User command: {run.data['command']}
Goal: {plan.get('goal', '')}

{patch_digest(run, workers)}

{COMMON_RULES}
Return only actionable findings ordered by severity with file:line references. Say "No blocking issues" if none."""


def review_prompt(run: Run, plan: Dict[str, Any], workers: List[Dict[str, Any]], security: Optional[str]) -> str:
    acceptance = "\n".join(f"- {a}" for a in plan.get("acceptance", []))
    sec = f"\nSecurity review findings:\n{clip(security, 8000)}\n" if security else ""
    return f"""You are the coordinator of a multi-agent coding harness reviewing round {run.data['round']} of {run.data['max_rounds']}.
User command: {run.data['command']}
Goal: {plan.get('goal', '')}
Acceptance criteria:
{acceptance}

{patch_digest(run, workers)}
{sec}
Approve only patches that are correct, safe, and within their owned paths. The working tree holds previous rounds' changes; you may inspect it read-only.
Set "done": true only if the acceptance criteria are met once approved patches are integrated.
Otherwise put precise remaining work in "feedback" for the next round.

Reply between BEGIN_JSON and END_JSON:
BEGIN_JSON
{{"approve": ["worker-name"], "done": false, "feedback": "what must change next round", "summary": "short result summary"}}
END_JSON
"""


def summary_prompt(run: Run, final: str, files: List[str], review: Dict[str, Any]) -> str:
    rounds = []
    for r in run.data["rounds"]:
        plan, rv = r.get("plan") or {}, r.get("review") or {}
        rounds.append(f"Round {r['round']}: goal: {plan.get('goal') or plan.get('summary', '')}\n"
                      f"  review: {rv.get('summary', '(none)')}" + (f"\n  feedback: {rv['feedback']}" if rv.get("feedback") else ""))
    workers = "\n".join(f"- {t['name']} ({t.get('provider') or t['role']}): {t['state']}"
                         + (f" - {clip(t['error'], 300)}" if t.get("error") else "")
                         for t in run.data["tasks"] if t.get("kind") == "work")
    unresolved = "" if review.get("done") else f"\nUnresolved (review said not done): {review.get('feedback') or '(no details)'}\n"
    return f"""You summarize the result of a multi-agent coding harness run for the user who requested it.
The working tree holds the final result; you may inspect it read-only.

User command:
{run.data['command']}

Rounds:
{chr(10).join(rounds) or '(none)'}

Workers:
{workers or '(none)'}
{unresolved}
Changed files ({len(files)}): {', '.join(files) or 'none'}

Final patch (not yet applied to the user's project):
```diff
{clip(final, 60000) or '(no changes)'}
```

{COMMON_RULES}

Write a concise Markdown report in the same language as the user command, with these sections:
1. Result: one or two sentences on what was achieved and whether the command is fully done.
2. Changes: per file or feature, what changed and why.
3. How to verify: concrete steps or commands the user can run after applying.
4. Caveats: unresolved work, risks, assumptions, or rejected/failed workers. Write "None" if there are none.
Base every claim on the patch and the notes above; do not invent changes. Reply with the report only."""


def write_report(run: Run, integration: Path, final: str, files: List[str], review: Dict[str, Any]) -> None:
    role = str(load_config().get("summary_role") or "")
    if role not in PROVIDER_CLI:
        return
    run.update(status="summarizing")
    task = run.add_task(name="summary", kind="summary", round=run.data["round"], role=role)
    try:
        report = call_agent(run, task, role, "read", integration, summary_prompt(run, final, files, review))
        (run.dir / "report.md").write_text(report.strip() + "\n")
        run.set_task(task, state="done", ended=now())
        run.update(report="report.md")
    except Stopped:
        raise
    except HarnessError as exc:  # the patch is ready either way; a missing report must not fail the run
        run.set_task(task, state="failed", error=str(exc), ended=now())
        run.event(f"summary unavailable: {exc}")


# ---------------------------------------------------------------------------
# Orchestration


def validate_plan(plan: Dict[str, Any], workdir: Path, max_workers: int, scope: List[str]) -> None:
    if plan.get("done"):
        return
    workers = plan.get("workers")
    if not isinstance(workers, list) or not 1 <= len(workers) <= max_workers:
        raise HarnessError(f"plan needs 1-{max_workers} workers")
    names, owned = set(), []
    protected = protected_paths(workdir)
    for w in workers:
        name = str(w.get("name", ""))
        if not NAME_RE.match(name) or name in names:
            raise HarnessError(f"invalid or duplicate worker name: {name!r}")
        names.add(name)
        if w.get("role") not in WORKER_ROLES:
            raise HarnessError(f"{name}: unknown role {w.get('role')!r}")
        if not str(w.get("assignment", "")).strip():
            raise HarnessError(f"{name}: empty assignment")
        paths = w.get("owned_paths")
        if not isinstance(paths, list) or not paths:
            raise HarnessError(f"{name}: owned_paths must be a non-empty list")
        clean = []
        for p in paths:
            p = str(p).strip().strip("/")
            if p.startswith("./"):
                p = p[2:]
            if not PATH_RE.match(p) or any(seg in (".", "..") for seg in p.split("/")):
                raise HarnessError(f"{name}: invalid path {p!r}")
            if is_protected(p, protected):
                raise HarnessError(f"{name}: protected path {p!r} cannot be assigned")
            if scope and not in_scope(p, scope):
                raise HarnessError(f"{name}: {p!r} is outside the task scope ({', '.join(scope)})")
            parent = p.rsplit("/", 1)[0] if "/" in p else ""
            if not (workdir / p).exists() and not (workdir / parent).is_dir():
                raise HarnessError(f"{name}: neither {p!r} nor its parent exists")
            for other_name, other in owned:
                if p == other or p.startswith(other + "/") or other.startswith(p + "/"):
                    raise HarnessError(f"overlapping owned paths: {other_name}:{other} and {name}:{p}")
            clean.append(p)
        for p in clean:
            owned.append((name, p))
        w["owned_paths"] = clean


def in_scope(path: str, owned: List[str]) -> bool:
    return any(path == o or path.startswith(o + "/") for o in owned)


def normalize_scope(repo: Path, paths: List[str]) -> List[str]:
    """Turn user-supplied files/folders (absolute or repo-relative) into repo-relative paths."""
    scope: List[str] = []
    for raw in paths:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            path = Path(part).expanduser()
            path = path.resolve() if path.is_absolute() else (repo / path).resolve()
            try:
                rel = path.relative_to(repo.resolve()).as_posix()
            except ValueError:
                raise HarnessError(f"path is outside the project: {part}")
            if rel in ("", "."):
                continue  # whole project == no restriction
            if not path.exists():
                raise HarnessError(f"path does not exist: {part}")
            if is_protected(rel, protected_paths(repo)):
                raise HarnessError(f"protected path cannot be targeted: {rel}")
            if rel not in scope:
                scope.append(rel)
    return scope


def run_worker(run: Run, task: Dict[str, Any], plan: Dict[str, Any], worker: Dict[str, Any], base: str) -> None:
    worktree = run.dir / "worktrees" / task["name"]
    try:
        git(run.repo, "worktree", "add", "--detach", str(worktree), base)
        call_agent(run, task, worker["role"], "write", worktree, worker_prompt(plan, worker))
        git(worktree, "add", "-N", "--all")
        changed = [p for p in git(worktree, "diff", "--name-only", base).splitlines()
                   if p and not p.startswith(TOOL_NOISE)]
        # Owned directories can still contain sensitive/protected files (e.g. app/.env); withhold those too.
        protected = protected_paths(worktree)
        outside = [p for p in changed if not in_scope(p, worker["owned_paths"]) or is_protected(p, protected)]
        patch_rel = f"results/{task['name']}.patch"
        patch = git(worktree, "diff", "--binary", base, "--", *worker["owned_paths"], strip=False) if changed else ""
        (run.dir / patch_rel).write_text(patch)
        if outside:
            (run.dir / f"results/{task['name']}.out-of-scope.txt").write_text("\n".join(outside) + "\n")
            run.set_task(task, state="out-of-scope", patch=patch_rel, changed_files=len(changed), ended=now())
            run.event(f"{task['name']}: changed files outside owned paths or protected; patch withheld")
        else:
            run.set_task(task, state="done", patch=patch_rel, changed_files=len(changed), ended=now())
            run.event(f"{task['name']}: done on {task['provider']} ({len(changed)} files)")
    except Stopped:
        run.set_task(task, state="stopped", ended=now())
    except Exception as exc:  # noqa: BLE001 - one worker failing must not kill the round
        run.set_task(task, state="failed", error=str(exc), ended=now())
        run.event(f"{task['name']}: failed: {exc}")
    finally:
        if worktree.exists():
            git(run.repo, "worktree", "remove", "--force", str(worktree), check=False)


def coordinator_call(run: Run, name: str, kind: str, workdir: Path, build_prompt, validate=None) -> Dict[str, Any]:
    error = None
    for attempt in (1, 2):
        task = run.add_task(name=name if attempt == 1 else f"{name}-retry", kind=kind, round=run.data["round"], role=COORDINATOR)
        try:
            data = extract_json(call_agent(run, task, COORDINATOR, "read", workdir, build_prompt(error)))
            if validate:
                validate(data)
            run.set_task(task, state="done", ended=now())
            return data
        except Stopped:
            run.set_task(task, state="stopped", ended=now())
            raise
        except HarnessError as exc:
            run.set_task(task, state="failed", error=str(exc), ended=now())
            error = str(exc)
            if attempt == 2 or "no available provider" in error or "failed (" in error:
                raise
            run.event(f"{name}: invalid output, retrying ({exc})")
    raise HarnessError("unreachable")


def run_round(run: Run, integration: Path, round_no: int, feedback: Optional[str]) -> Dict[str, Any]:
    cfg = load_config()
    run.update(round=round_no, status="planning")
    plan = coordinator_call(
        run, f"r{round_no}-plan", "plan", integration,
        lambda err: plan_prompt(run, round_no, feedback, err),
        lambda p: validate_plan(p, integration, int(cfg["max_workers"]), run.data.get("scope") or []),
    )
    record: Dict[str, Any] = {"round": round_no, "plan": plan, "review": None}
    with run.lock:
        run.data["rounds"].append(record)
        run.save()
    if plan.get("done"):
        return {"done": True, "summary": plan.get("summary", ""), "approve": []}

    run.update(status="working")
    base = git(integration, "rev-parse", "HEAD")
    tasks = []
    threads = []
    for worker in plan["workers"]:
        task = run.add_task(name=f"r{round_no}-{worker['name']}", kind="work", round=round_no,
                            role=worker["role"], owned_paths=worker["owned_paths"])
        tasks.append(task)
        thread = threading.Thread(target=run_worker, args=(run, task, plan, worker, base), daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    run.check_stop()

    reviewable = [t for t in tasks if t["state"] == "done" and t.get("changed_files")]
    security = None
    if reviewable and plan.get("security_review"):
        run.update(status="security-review")
        task = run.add_task(name=f"r{round_no}-security", kind="security", round=round_no, role="claude")
        try:
            security = call_agent(run, task, "claude", "read", integration, security_prompt(run, plan, reviewable))
            run.set_task(task, state="done", ended=now())
        except Stopped:
            raise
        except HarnessError as exc:
            run.set_task(task, state="failed", error=str(exc), ended=now())
            security = f"(security review unavailable: {exc}) Be conservative."

    run.update(status="reviewing")
    review = coordinator_call(run, f"r{round_no}-review", "review", integration,
                              lambda err: review_prompt(run, plan, tasks, security))
    record["review"] = review
    approved = {str(n) for n in review.get("approve", [])}
    run.update(status="integrating")
    applied = []
    for task in reviewable:
        short = task["name"].split("-", 1)[1]
        if short not in approved and task["name"] not in approved:
            run.set_task(task, state="rejected")
            continue
        patch = run.dir / task["patch"]
        check = subprocess.run(["git", "-C", str(integration), "apply", "--3way", "--index", str(patch)],
                               capture_output=True, text=True)
        if check.returncode != 0:
            run.set_task(task, state="conflict", error=check.stderr.strip())
            run.event(f"{task['name']}: patch conflict, not integrated")
            git(integration, "reset", "--hard", "-q", check=False)
            continue
        # Commit each patch right away so a later conflict's reset cannot discard it.
        git(integration, *GIT_IDENT, "commit", "-q", "--no-verify", "-m", f"ai-harness round {round_no}: {task['name']}")
        run.set_task(task, state="integrated")
        applied.append(task["name"])
    missing = [t for t in tasks if t["name"] not in applied
               and (t["name"] in approved or t["name"].split("-", 1)[1] in approved)
               and t["state"] != "done"]
    if not applied and not missing and review.get("done") and any(t["state"] != "done" for t in tasks):
        missing = [t for t in tasks if t["state"] != "done"]
    if missing and review.get("done"):
        review["done"] = False
        notes = "; ".join(f"{t['name']} was {t['state']}" + (f" ({t.get('error')})" if t.get("error") else "") for t in missing)
        review["feedback"] = (review.get("feedback") or "") + f"\nNot integrated: {notes}. Redo this work within owned paths."
        run.event(f"round {round_no}: review said done but {len(missing)} patch(es) were not integrated; continuing")
    run.event(f"round {round_no}: integrated {', '.join(applied) or 'nothing'}")
    return review


def execute(run: Run) -> None:
    def on_signal(signum, _frame):
        run.stopping = True
        with run.lock:
            children = list(run.children)
        for child in children:
            kill_group(child)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    run.update(pid=os.getpid(), status="snapshot")
    try:
        ensure_runs_ignored(run.repo)
        base = snapshot(run.repo, run.id)
        integration = run.dir / "integration"
        git(run.repo, "worktree", "add", "--detach", str(integration), base)
        run.update(base=base)
        run.event(f"snapshot {base[:10]}; starting")
        feedback = None
        review: Dict[str, Any] = {}
        for round_no in range(1, int(run.data["max_rounds"]) + 1):
            review = run_round(run, integration, round_no, feedback)
            if review.get("done"):
                break
            feedback = review.get("feedback") or "Previous round incomplete; continue the command."
        head = git(integration, "rev-parse", "HEAD")
        final = git(integration, "diff", "--binary", base, head, strip=False) if head != base else ""
        (run.dir / "final.patch").write_text(final)
        files = git(integration, "diff", "--name-only", base, head).splitlines() if final else []
        write_report(run, integration, final, files, review)
        run.update(status="ready" if final else "no-changes", ended=now(), final_files=files,
                   summary=review.get("summary"), feedback=None if review.get("done") else review.get("feedback"),
                   completed=bool(review.get("done")))
        run.event("finished: " + (f"{len(files)} files ready to apply" if final else "no changes"))
    except Stopped:
        run.update(status="stopped", ended=now())
        run.event("stopped by user")
    except Exception as exc:  # noqa: BLE001
        run.update(status="failed", error=str(exc), ended=now())
        run.event(f"failed: {exc}")
        raise
    finally:
        cleanup_worktrees(run, keep_integration=True)


def cleanup_worktrees(run: Run, keep_integration: bool) -> None:
    wt_dir = run.dir / "worktrees"
    for path in wt_dir.iterdir() if wt_dir.exists() else []:
        git(run.repo, "worktree", "remove", "--force", str(path), check=False)
    if not keep_integration and (run.dir / "integration").exists():
        git(run.repo, "worktree", "remove", "--force", str(run.dir / "integration"), check=False)
    git(run.repo, "worktree", "prune", check=False)


# ---------------------------------------------------------------------------
# CLI


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32].strip("-")
    return slug or "task"


def cmd_start(args: argparse.Namespace, foreground: bool) -> None:
    repo = repo_root(args.repo)
    if not (HOME_DIR / "config.json").exists():
        cmd_init(args)
    command = args.command_text.strip()
    if not command:
        raise HarnessError("command is empty")
    rounds = args.rounds or int(load_config()["max_rounds"])
    scope = normalize_scope(repo, args.path or [])
    run = Run.create(repo, command, args.slug or slugify(command), rounds, scope)
    if foreground:
        execute(run)
        return
    log = (run.dir / "engine.log").open("w")
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "resume", "--repo", str(repo), run.id],
                            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    run.update(pid=proc.pid)
    print(run.id)


def cmd_resume(args: argparse.Namespace) -> None:
    execute(Run(repo_root(args.repo), args.run_id))


def load_state(repo: Path, run_id: Optional[str]) -> Dict[str, Any]:
    ids = sorted((p.parent.name for p in runs_dir(repo).glob("*/state.json")), reverse=True)
    if not ids:
        raise HarnessError("no runs yet")
    run_id = run_id or ids[0]
    return json.loads((runs_dir(repo) / run_id / "state.json").read_text())


def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


ACTIVE = {"queued", "snapshot", "planning", "working", "security-review", "reviewing", "integrating", "summarizing"}


def cmd_status(args: argparse.Namespace) -> None:
    state = load_state(repo_root(args.repo), args.run_id)
    status = state["status"]
    if status in ACTIVE and not pid_alive(state.get("pid")):
        status += " (engine not running)"
    print(f"Run     {state['id']}\nStatus  {status}  round {state['round']}/{state['max_rounds']}")
    print(f"Command {state['command']}")
    print(f"Scope   {', '.join(state.get('scope') or []) or '(whole project)'}\n")
    print(f"{'TASK':<22} {'ROLE':<12} {'PROVIDER':<12} {'STATE':<13} {'TIME':<7} FILES")
    for t in state["tasks"]:
        end = t.get("ended") or (now() if t.get("started") else None)
        secs = int(end - t["started"]) if end and t.get("started") else None
        fb = " (fallback)" if t.get("provider") and t["provider"] != t["role"] else ""
        print(f"{t['name']:<22} {t['role']:<12} {(t.get('provider') or '-') + fb:<12} {t['state']:<13} "
              f"{(str(secs) + 's') if secs is not None else '-':<7} {t.get('changed_files') if t.get('changed_files') is not None else '-'}")
    for e in state["events"][-5:]:
        print(f"  {dt.datetime.fromtimestamp(e['t']):%H:%M:%S} {e['msg']}")
    report = runs_dir(repo_root(args.repo)) / state["id"] / (state.get("report") or "")
    if state.get("report") and report.is_file():
        print("\n" + report.read_text(errors="replace").rstrip())


def cmd_list(args: argparse.Namespace) -> None:
    for path in sorted(runs_dir(repo_root(args.repo)).glob("*/state.json"), reverse=True):
        state = json.loads(path.read_text())
        print(f"{state['id']:<40} {state['status']:<16} {state['command'][:60]}")


def cmd_apply(args: argparse.Namespace) -> None:
    repo = repo_root(args.repo)
    run_dir = runs_dir(repo) / args.run_id
    state = json.loads((run_dir / "state.json").read_text())
    patch = run_dir / "final.patch"
    if state["status"] != "ready" or not patch.exists() or patch.stat().st_size == 0:
        raise HarnessError(f"run is not ready to apply (status {state['status']})")
    if subprocess.run(["git", "-C", str(repo), "apply", "--check", str(patch)], capture_output=True).returncode == 0:
        git(repo, "apply", str(patch))
    else:
        git(repo, "apply", "--3way", str(patch))
        print("Applied with 3-way merge; files are staged. Resolve any conflict markers.")
    write_json(run_dir / "state.json", {**state, "status": "applied", "applied_at": now()})
    print(f"Applied {len(state.get('final_files', []))} files to {repo}")


def cmd_stop(args: argparse.Namespace) -> None:
    state = load_state(repo_root(args.repo), args.run_id)
    if not pid_alive(state.get("pid")):
        raise HarnessError("engine is not running")
    os.kill(state["pid"], signal.SIGTERM)
    print(f"Stop requested for {state['id']}")


def cmd_cleanup(args: argparse.Namespace) -> None:
    repo = repo_root(args.repo)
    run = Run(repo, args.run_id)
    if run.data["status"] in ACTIVE and pid_alive(run.data.get("pid")):
        raise HarnessError("run is still active; stop it first")
    cleanup_worktrees(run, keep_integration=False)
    git(repo, "update-ref", "-d", f"refs/ai-harness/{run.id}", check=False)
    if args.delete:
        shutil.rmtree(run.dir)
    print(f"Cleaned {run.id}")


KIMI_AGENTS = {
    "harness-kimi-writer.md": """---
description: AI harness worker. Implements only the assigned OWNED_PATHS.
mode: primary
temperature: 0.1
permission:
  edit: allow
  bash: deny
---

You are a worker in a multi-agent coding harness. Modify only the OWNED_PATHS named in the task. Never read secrets, credentials, or .env files. Do not commit or push. Report changed files, assumptions, and checks actually performed.
""",
    "harness-kimi-reader.md": """---
description: AI harness read-only planner/reviewer fallback.
mode: primary
temperature: 0.1
permission:
  edit: deny
  bash: deny
---

You are a read-only coordinator for a multi-agent coding harness. Inspect files but never edit them. Never read secrets, credentials, or .env files. Follow the requested output format exactly.
""",
}


def cmd_init(_args: argparse.Namespace) -> None:
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        write_json(CONFIG_PATH, DEFAULT_CONFIG)
        print(f"Wrote {CONFIG_PATH}")
    OPENCODE_AGENTS.mkdir(parents=True, exist_ok=True)
    for name, body in KIMI_AGENTS.items():
        (OPENCODE_AGENTS / name).write_text(body)
    print(f"Installed OpenCode agents in {OPENCODE_AGENTS}")


def cmd_config(args: argparse.Namespace) -> None:
    cfg = load_config()
    if args.provider:
        if args.provider not in cfg["providers"]:
            raise HarnessError(f"unknown provider {args.provider}")
        user = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else json.loads(json.dumps(DEFAULT_CONFIG))
        user.setdefault("providers", {}).setdefault(args.provider, {})["enabled"] = args.state == "on"
        HOME_DIR.mkdir(parents=True, exist_ok=True)
        write_json(CONFIG_PATH, user)
        cfg = load_config()
    for name, p in cfg["providers"].items():
        chain = " -> ".join(cfg["fallback"].get(name, []))
        print(f"{name:<12} {'on ' if p.get('enabled') else 'off'} {p.get('model') or '(default)':<40} fallback: {chain}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-agent AI harness with model fallback")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("start", "run"):
        p = sub.add_parser(name, help="start a run in the background" if name == "start" else "run in the foreground")
        p.add_argument("--repo", default=".")
        p.add_argument("--rounds", type=int)
        p.add_argument("--slug")
        p.add_argument("--path", action="append", metavar="PATH",
                       help="limit changes to this file or folder (repeatable or comma-separated)")
        p.add_argument("command_text", metavar="COMMAND")
    p = sub.add_parser("resume", help=argparse.SUPPRESS)
    p.add_argument("--repo", default=".")
    p.add_argument("run_id")
    for name in ("status", "stop"):
        p = sub.add_parser(name)
        p.add_argument("--repo", default=".")
        p.add_argument("run_id", nargs="?")
    p = sub.add_parser("list")
    p.add_argument("--repo", default=".")
    p = sub.add_parser("apply", help="apply a finished run's final.patch to the project")
    p.add_argument("--repo", default=".")
    p.add_argument("run_id")
    p = sub.add_parser("cleanup")
    p.add_argument("--repo", default=".")
    p.add_argument("--delete", action="store_true", help="also delete the run directory")
    p.add_argument("run_id")
    sub.add_parser("init", help="write default config and install OpenCode agents")
    p = sub.add_parser("config", help="show config or toggle a provider")
    p.add_argument("provider", nargs="?")
    p.add_argument("state", nargs="?", choices=("on", "off"))
    args = parser.parse_args()

    try:
        if args.cmd in ("start", "run"):
            cmd_start(args, foreground=args.cmd == "run")
        else:
            {"resume": cmd_resume, "status": cmd_status, "stop": cmd_stop, "list": cmd_list, "apply": cmd_apply,
             "cleanup": cmd_cleanup, "init": cmd_init, "config": cmd_config}[args.cmd](args)
    except HarnessError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
