"""In-app updates for installs that run from a git checkout.

The one-line installer (install.sh) clones the repository and installs it in
editable mode, so the running package lives inside the checkout. An update
fast-forwards the checkout to its upstream branch, builds the frontend into a
staging directory, syncs the Python dependencies, swaps the new frontend in and
then restarts the server in place with os.execv. The PID stays the same, so
systemd, nohup and tmux all keep tracking the process.

A wheel install (pip install of a built package) has no checkout to update, so
check() reports it as unsupported.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import signal
import sys
import threading
import time
from collections import deque
from contextlib import suppress
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

from cptr.utils.changelog import parse_changelog_text

log = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PACKAGE_DIR / "frontend"
BUILD_DIR = FRONTEND_DIR / "build"
STAGING_DIR = FRONTEND_DIR / "build.next"
OLD_BUILD_DIR = FRONTEND_DIR / "build.old"

CHECK_TTL = 600  # seconds a check result is reused before fetching again
MAX_COMMITS = 50
GIT_TIMEOUT = 120
STEP_TIMEOUT = 20 * 60
STEPS = ("fetch", "pull", "frontend_deps", "frontend_build", "backend_deps", "restart")


class UpdateError(Exception):
    """An update problem the UI explains; `code` picks the message."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


# ── Install layout ───────────────────────────────────────────


def source_dir() -> Path | None:
    """The git checkout the running package was installed from, if any."""
    root = PACKAGE_DIR.parent
    if (root / ".git").exists() and (root / "pyproject.toml").is_file():
        return root
    return None


def installed_version() -> str:
    try:
        return pkg_version("cptr")
    except Exception:
        return "dev"


def _search_path(repo: Path | None) -> str:
    """PATH for update commands: the installer's Node and uv come first."""
    home = Path.home()
    extra = [home / ".local" / "bin", home / ".cargo" / "bin"]
    if repo:
        extra.insert(0, repo / ".tools" / "node" / "bin")
    extra += [Path("/usr/local/bin"), Path("/opt/homebrew/bin")]
    parts = [str(p) for p in extra if p.is_dir()]
    parts += os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(dict.fromkeys(p for p in parts if p))


def _command_env(repo: Path | None) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = _search_path(repo)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["npm_config_fund"] = "false"
    env["npm_config_audit"] = "false"
    env["npm_config_update_notifier"] = "false"
    # Let uv find the checkout's own .venv rather than whatever was activated.
    env.pop("VIRTUAL_ENV", None)
    return env


def _backend_deps_command(repo: Path, env: dict[str, str]) -> list[str]:
    uv = shutil.which("uv", path=env["PATH"])
    in_project_venv = Path(sys.prefix).resolve() == (repo / ".venv").resolve()
    if uv and in_project_venv:
        return [uv, "sync", "--frozen", "--extra", "all"]
    if uv:
        return [uv, "pip", "install", "--python", sys.executable, "-e", f"{repo}[all]"]
    return [sys.executable, "-m", "pip", "install", "--quiet", "-e", f"{repo}[all]"]


# ── Commands ─────────────────────────────────────────────────


async def _git(repo: Path, *args: str, timeout: float = GIT_TIMEOUT) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(repo),
            env=_command_env(repo),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise UpdateError("git_missing") from error
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError as error:
        proc.kill()
        await proc.wait()
        raise UpdateError("timeout", f"git {args[0]}") from error
    if proc.returncode != 0:
        message = err.decode(errors="replace").strip() or out.decode(errors="replace").strip()
        raise UpdateError("git_failed", f"git {' '.join(args)}: {message}")
    return out.decode(errors="replace").rstrip()


async def _run(args: list[str], cwd: Path, env: dict[str, str]) -> None:
    """Run one update step's command, streaming its output into the log."""
    _log_line("$ " + " ".join([Path(args[0]).name, *args[1:]]))
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
        limit=1 << 20,
    )
    tail: deque[str] = deque(maxlen=20)

    async def pump() -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = _ANSI.sub("", raw.decode(errors="replace")).rstrip()
            if line:
                tail.append(line)
                _log_line(line)

    try:
        await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), STEP_TIMEOUT)
    except asyncio.TimeoutError as error:
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        raise UpdateError("timeout", Path(args[0]).name) from error
    if proc.returncode != 0:
        raise UpdateError(
            "command_failed",
            f"{Path(args[0]).name} {' '.join(args[1:3])} exited with {proc.returncode}\n"
            + "\n".join(tail),
        )


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


# ── Checking ─────────────────────────────────────────────────


def _version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value.split("+")[0])[:4])


def _pyproject_version(text: str) -> str | None:
    section = ""
    for line in text.splitlines():
        header = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if header:
            section = header.group(1).strip()
        elif section == "project":
            match = re.match(r'^\s*version\s*=\s*"([^"]+)"', line)
            if match:
                return match.group(1)
    return None


def _public_url(remote_url: str) -> str | None:
    """A browsable https URL for the remote, with any credentials removed."""
    url = remote_url.strip()
    ssh = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+)$", url)
    if ssh:
        url = f"https://{ssh.group(1)}/{ssh.group(2)}"
    match = re.match(r"^https?://(?:[^@/]+@)?(.+)$", url)
    if not match:
        return None
    return "https://" + re.sub(r"\.git/?$", "", match.group(1)).rstrip("/")


async def _upstream(repo: Path) -> tuple[str, str]:
    try:
        ref = await _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        remote, _, branch = ref.partition("/")
        if remote and branch:
            return remote, branch
    except UpdateError:
        pass
    if "origin" in (await _git(repo, "remote")).split():
        return "origin", "main"
    raise UpdateError("no_upstream")


def _active_chats() -> int:
    try:
        from cptr.utils.chat_task import get_active_chat_ids

        return len(get_active_chat_ids())
    except Exception:
        return 0


_check_cache: dict[str, Any] | None = None
_check_lock = asyncio.Lock()


async def check(refresh: bool = False) -> dict[str, Any]:
    """Compare the checkout with its upstream branch (fetching it first)."""
    global _check_cache
    async with _check_lock:
        cached = _check_cache
        if (
            not refresh
            and cached is not None
            and time.time() - cached["checked_at"] < CHECK_TTL
            and cached["current"]["version"] == installed_version()
        ):
            return {**cached, "active_chats": _active_chats()}
        _check_cache = await _check()
        return _check_cache


async def _check() -> dict[str, Any]:
    result: dict[str, Any] = {
        "supported": False,
        "available": False,
        "reason": None,
        "detail": "",
        "current": {"version": installed_version(), "commit": None, "date": None},
        "latest": None,
        "behind": 0,
        "commits": [],
        "notes": {},
        "dirty_files": [],
        "branch": None,
        "repo_url": None,
        "can_restart": can_restart(),
        "active_chats": _active_chats(),
        "checked_at": time.time(),
    }
    repo = source_dir()
    if repo is None:
        result["reason"] = "not_git"
        return result
    if not shutil.which("git", path=_search_path(repo)):
        result["reason"] = "git_missing"
        return result
    result["supported"] = True

    try:
        head = await _git(repo, "rev-parse", "HEAD")
        result["current"]["commit"] = head
        result["current"]["date"] = await _git(repo, "show", "-s", "--format=%cI", head)
        remote, branch = await _upstream(repo)
        result["branch"] = branch
        with suppress(UpdateError):
            result["repo_url"] = _public_url(await _git(repo, "remote", "get-url", remote))

        try:
            await _git(repo, "fetch", "--quiet", "--no-tags", remote, branch, timeout=90)
        except UpdateError as error:
            raise UpdateError("fetch_failed", error.detail) from error

        target = f"refs/remotes/{remote}/{branch}"
        latest = await _git(repo, "rev-parse", target)
        behind = int(await _git(repo, "rev-list", "--count", f"HEAD..{target}"))
        ahead = int(await _git(repo, "rev-list", "--count", f"{target}..HEAD"))
        latest_version = result["current"]["version"]
        with suppress(UpdateError):
            latest_version = (
                _pyproject_version(await _git(repo, "show", f"{target}:pyproject.toml"))
                or latest_version
            )
        result["latest"] = {
            "version": latest_version,
            "commit": latest,
            "date": await _git(repo, "show", "-s", "--format=%cI", latest),
        }
        result["behind"] = behind
        result["available"] = behind > 0

        if behind:
            raw = await _git(
                repo,
                "log",
                f"--max-count={MAX_COMMITS}",
                "--no-merges",
                "--format=%H%x1f%s%x1f%cI",
                f"HEAD..{target}",
            )
            result["commits"] = [
                {"commit": sha, "subject": subject, "date": date}
                for sha, subject, date in (
                    line.split("\x1f", 2) for line in raw.splitlines() if line.count("\x1f") == 2
                )
            ]
            with suppress(UpdateError):
                notes = parse_changelog_text(await _git(repo, "show", f"{target}:CHANGELOG.md"))
                current_key = _version_key(result["current"]["version"])
                result["notes"] = {
                    ver: data for ver, data in notes.items() if _version_key(ver) > current_key
                }

        dirty = await _git(repo, "status", "--porcelain", "--untracked-files=no")
        result["dirty_files"] = [line[3:] for line in dirty.splitlines() if line.strip()]
        if ahead:
            result["reason"] = "diverged"
        elif result["dirty_files"]:
            result["reason"] = "dirty"
    except UpdateError as error:
        result["reason"] = error.code
        result["detail"] = error.detail
    return result


# ── Applying ─────────────────────────────────────────────────

_log: deque[str] = deque(maxlen=400)
_state: dict[str, Any] = {"status": "idle"}
_task: asyncio.Task | None = None


def _log_line(line: str) -> None:
    _log.append(line[:2000])


def status() -> dict[str, Any]:
    return {**_state, "log": list(_log)[-120:]}


def _step(key: str, state: str) -> None:
    for step in _state["steps"]:
        if step["key"] == key:
            step["status"] = state
    if state == "running":
        _state["step"] = key


def start_update() -> dict[str, Any]:
    """Start updating in the background; progress comes from status()."""
    global _task
    if _state.get("status") in ("running", "restarting"):
        raise UpdateError("busy")
    repo = source_dir()
    if repo is None:
        raise UpdateError("not_git")
    _log.clear()
    _state.clear()
    _state.update(
        status="running",
        step="fetch",
        steps=[{"key": key, "status": "pending"} for key in STEPS],
        error=None,
        detail="",
        rolled_back=False,
        rollback_failed=False,
        from_commit=None,
        to_commit=None,
        to_version=None,
        restart_required=False,
        started=time.time(),
    )
    _task = asyncio.create_task(_apply(repo))
    return status()


async def _apply(repo: Path) -> None:
    env = _command_env(repo)
    old_head: str | None = None
    pulled = synced = False
    try:
        _step("fetch", "running")
        info = await check(refresh=True)
        if (info["reason"] and info["reason"] not in ("dirty", "diverged")) or not info["latest"]:
            raise UpdateError(info["reason"] or "unknown", info["detail"])
        if not info["available"]:
            raise UpdateError("up_to_date")
        if info["reason"]:
            raise UpdateError(info["reason"], "\n".join(info["dirty_files"]))
        npm = shutil.which("npm", path=env["PATH"])
        if not npm:
            raise UpdateError("npm_missing")
        deps_command = _backend_deps_command(repo, env)
        old_head = info["current"]["commit"]
        target = info["latest"]["commit"]
        _state.update(from_commit=old_head, to_commit=target, to_version=info["latest"]["version"])
        _step("fetch", "done")

        _step("pull", "running")
        changed = set((await _git(repo, "diff", "--name-only", old_head, target)).splitlines())
        _log_line(f"$ git merge --ff-only {target[:7]}")
        await _git(repo, "merge", "--ff-only", target)
        pulled = True
        _log_line(f"{old_head[:7]}..{target[:7]}, {len(changed)} files changed")
        _step("pull", "done")

        _step("frontend_deps", "running")
        lock = "cptr/frontend/package-lock.json"
        if lock in changed or not (FRONTEND_DIR / "node_modules").is_dir():
            await _run([npm, "ci"], FRONTEND_DIR, env)
            _step("frontend_deps", "done")
        else:
            _log_line("package-lock.json unchanged; keeping node_modules")
            _step("frontend_deps", "skipped")

        _step("frontend_build", "running")
        shutil.rmtree(STAGING_DIR, ignore_errors=True)
        await _run(
            [npm, "run", "build"], FRONTEND_DIR, {**env, "CPTR_FRONTEND_OUT": STAGING_DIR.name}
        )
        if not (STAGING_DIR / "index.html").is_file():
            raise UpdateError("build_missing")
        _step("frontend_build", "done")

        _step("backend_deps", "running")
        synced = True
        await _run(deps_command, repo, env)
        _step("backend_deps", "done")

        shutil.rmtree(OLD_BUILD_DIR, ignore_errors=True)
        if BUILD_DIR.exists():
            BUILD_DIR.rename(OLD_BUILD_DIR)
        STAGING_DIR.rename(BUILD_DIR)
        shutil.rmtree(OLD_BUILD_DIR, ignore_errors=True)
    except Exception as error:
        code = error.code if isinstance(error, UpdateError) else "unknown"
        detail = error.detail if isinstance(error, UpdateError) else str(error)
        log.warning("Update failed (%s): %s", code, detail)
        _log_line(f"✗ {(detail or code).splitlines()[0]}")
        for step in _state["steps"]:
            if step["status"] == "running":
                step["status"] = "failed"
        _state.update(status="failed", error=code, detail=detail)
        shutil.rmtree(STAGING_DIR, ignore_errors=True)
        if pulled and old_head:
            await _roll_back(repo, old_head, env, synced)
        return

    global _check_cache
    _check_cache = None
    if not can_restart():
        _state.update(status="done", restart_required=True)
        return
    _step("restart", "running")
    _state["status"] = "restarting"
    log.info("Update installed (%s); restarting", _state["to_commit"])
    # Give the browser time to see "restarting" before the server goes away.
    asyncio.get_running_loop().call_later(1.0, request_restart)


async def _roll_back(repo: Path, old_head: str, env: dict[str, str], synced: bool) -> None:
    try:
        _log_line(f"$ git reset --keep {old_head[:7]}")
        await _git(repo, "reset", "--keep", old_head)
        if synced:
            await _run(_backend_deps_command(repo, env), repo, env)
        _state["rolled_back"] = True
    except Exception as error:
        log.error("Rolling back the update failed: %s", error)
        _state["rollback_failed"] = True
        _log_line(f"✗ rollback: {error}")


# ── Restarting ───────────────────────────────────────────────

_server: Any = None
_restart_requested = False


def register_server(server: Any) -> None:
    """cptr run hands over its uvicorn.Server so an update can restart it."""
    global _server
    _server = server


def can_restart() -> bool:
    return _server is not None


def request_restart() -> None:
    """Shut the server down gracefully; cptr run then re-executes itself."""
    global _restart_requested
    if _server is None:
        return
    _restart_requested = True
    _server.should_exit = True
    threading.Thread(target=_restart_watchdog, daemon=True).start()


def _restart_watchdog() -> None:
    # A long-lived connection can hold up a graceful shutdown; don't wait forever.
    time.sleep(10)
    _server.force_exit = True
    time.sleep(15)
    log.warning("Graceful shutdown is taking too long; restarting anyway")
    exec_self()


def restart_if_requested() -> None:
    if _restart_requested:
        exec_self()


def exec_self() -> None:
    argv = list(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])
    os.environ["CPTR_RESTARTED"] = "1"
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, argv)
