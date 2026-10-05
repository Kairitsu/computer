"""GitHub CLI operations via subprocess."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any

from cptr.utils.identity import ExecutionIdentity, env_for, preexec_for


class GhError(Exception):
    def __init__(self, message: str, returncode: int = 1):
        super().__init__(message)
        self.returncode = returncode


def _gh_path() -> str | None:
    return shutil.which("gh")


async def run_gh(
    args: list[str],
    *,
    identity: ExecutionIdentity,
    cwd: str | None = None,
    check: bool = True,
    timeout: float = 20,
) -> tuple[int, str, str]:
    gh = _gh_path()
    if not gh:
        raise GhError("GitHub CLI is not installed")
    work_dir = cwd or identity.home
    env = env_for(identity, work_dir) if identity.is_pam else os.environ.copy()
    env["GH_PROMPT_DISABLED"] = "1"
    proc = await asyncio.create_subprocess_exec(
        gh,
        *args,
        cwd=work_dir,
        env=env,
        preexec_fn=preexec_for(identity) if identity.is_pam else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise GhError("GitHub CLI command timed out") from exc
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise GhError((stderr or stdout).strip() or "GitHub CLI command failed", proc.returncode)
    return proc.returncode or 0, stdout, stderr


async def version(identity: ExecutionIdentity) -> str | None:
    if not _gh_path():
        return None
    code, out, _ = await run_gh(["--version"], identity=identity, check=False, timeout=5)
    if code != 0:
        return None
    return out.splitlines()[0].strip() if out.strip() else None


async def auth_status(identity: ExecutionIdentity, hostname: str | None = None) -> dict[str, Any]:
    gh_version = await version(identity)
    if gh_version is None:
        return {"installed": False, "version": None, "hosts": {}}
    args = ["auth", "status", "--json", "hosts"]
    if hostname:
        args.extend(["--hostname", hostname])
    code, out, err = await run_gh(args, identity=identity, check=False, timeout=10)
    if not out.strip():
        return {"installed": True, "version": gh_version, "hosts": {}, "message": err.strip()}
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {"installed": True, "version": gh_version, "hosts": {}, "message": err.strip()}
    return {"installed": True, "version": gh_version, "hosts": data.get("hosts") or {}}
