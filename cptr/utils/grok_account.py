"""Grok CLI login for Settings → Usage: sign in, sign out and saved accounts.

Ported from Grok App (`src-tauri/src/account.rs`, `account_profiles.rs`). The Grok
CLI owns `auth.json`: cptr runs `grok login` / `grok logout` and copies snapshots
of that file to switch between saved accounts. Tokens are never logged or returned.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from cptr.env import DATA_DIR
from cptr.utils import supergrok

log = logging.getLogger(__name__)

ACCOUNTS_DIR = DATA_DIR / "grok-accounts"
ACCOUNTS_INDEX = ACCOUNTS_DIR / "index.json"
LOGIN_TIMEOUT_SECONDS = 15 * 60
LOGOUT_TIMEOUT_SECONDS = 20
# Credentials can land a moment after `grok login` exits.
LOGIN_SETTLE_SECONDS = 10

URL_RE = re.compile(r"https?://[^\s\"'<>]+")
CODE_RE = re.compile(r"(?i:user[_ ]?code|code)\s*[:=]?\s*([A-Z0-9]{4,}(?:-[A-Z0-9]{4,})*)\b")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class GrokAccountError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ── CLI ─────────────────────────────────────────────────────


async def grok_cli() -> tuple[str | None, str | None]:
    """(command, HOME override) of the Grok agent profile, else `grok` on PATH."""
    command = home = None
    try:
        from cptr.utils.agents.models import get_raw_agent_profiles

        profiles = await get_raw_agent_profiles()
    except Exception:  # noqa: BLE001 — config may not be ready; fall back to PATH
        profiles = None
    for profile in profiles if isinstance(profiles, list) else []:
        if isinstance(profile, dict) and profile.get("agent") == "grok":
            command = str(profile.get("command") or "").strip() or None
            home = str(profile.get("home") or "").strip() or None
            break
    resolved = shutil.which(os.path.expanduser(command or "grok"))
    return resolved, home


def _cli_env(home: str | None) -> dict[str, str]:
    env = os.environ.copy()
    if home:
        env["HOME"] = os.path.expanduser(home)
    env["GROK_OAUTH2_REFERRER"] = "cptr"
    env["NO_COLOR"] = "1"
    return env


def primary_auth_path(home: str | None) -> Path:
    """The auth.json the Grok agent reads, where a switched account is written."""
    grok_home = os.environ.get("GROK_HOME")
    if grok_home:
        return Path(grok_home).expanduser() / "auth.json"
    return Path(home or Path.home()).expanduser() / ".grok" / "auth.json"


async def _auth_homes() -> list[str]:
    return await supergrok.grok_profile_homes()


def _mtimes(paths: list[Path]) -> dict[Path, float | None]:
    result: dict[Path, float | None] = {}
    for path in paths:
        try:
            result[path] = path.stat().st_mtime
        except OSError:
            result[path] = None
    return result


async def after_auth_change() -> None:
    """Forget state tied to the previous login."""
    from cptr.utils.agents.grok import close_idle_grok_sessions

    supergrok.invalidate_cache()
    await close_idle_grok_sessions()


# ── Sign in ─────────────────────────────────────────────────


@dataclass
class GrokLogin:
    method: str
    proc: asyncio.subprocess.Process
    started_at: float
    before: dict[Path, float | None]
    homes: list[str]
    url: str = ""
    code: str = ""
    output: list[str] = field(default_factory=list)
    state: str = "running"  # running | succeeded | failed | cancelled | expired
    message: str = ""


_login: GrokLogin | None = None
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def parse_login_line(login: GrokLogin, line: str) -> None:
    """Pick the sign-in URL and device code out of one line of CLI output."""
    if not login.url:
        match = URL_RE.search(line)
        if match:
            login.url = match.group(0).rstrip(".,;)")
    if not login.code:
        match = CODE_RE.search(line)
        if match:
            login.code = match.group(1)


async def _read_stream(login: GrokLogin, stream: asyncio.StreamReader | None) -> None:
    if stream is None:
        return
    while True:
        raw = await stream.readline()
        if not raw:
            return
        line = ANSI_RE.sub("", raw.decode("utf-8", "replace")).strip()
        if not line:
            continue
        login.output = [*login.output[-39:], line[:500]]
        parse_login_line(login, line)


def _failure_message(login: GrokLogin) -> str:
    detail = next((line for line in reversed(login.output) if line), "")
    lower = " ".join(login.output).lower()
    if "access denied" in lower or "failed to generate authentication" in lower:
        return "xAI could not generate an authentication code (access denied). Try device code."
    if detail:
        return detail if len(detail) <= 240 else f"{detail[:240]}…"
    return "Sign-in finished without new credentials."


async def _run_login(login: GrokLogin) -> None:
    proc = login.proc
    try:
        await asyncio.wait_for(
            asyncio.gather(
                _read_stream(login, proc.stdout), _read_stream(login, proc.stderr), proc.wait()
            ),
            timeout=LOGIN_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        if proc.returncode is None:
            proc.kill()
        if login.state == "running":
            login.state = "expired"
            login.message = "Sign-in timed out."
        return
    if login.state != "running":
        return

    paths = list(login.before)
    deadline = time.monotonic() + (LOGIN_SETTLE_SECONDS if proc.returncode == 0 else 0)
    while True:
        changed = _mtimes(paths) != login.before
        profile = supergrok.read_auth_profile(login.homes)
        if (changed and profile["signed_in"]) or time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.5)

    if login.state != "running":
        return
    if changed and profile["signed_in"]:
        login.state = "succeeded"
        login.message = profile["email"] or profile["display_name"] or ""
        await after_auth_change()
        try:
            save_current_account(login.homes)
        except GrokAccountError as error:
            log.info("Grok account snapshot skipped: %s", error)
    else:
        login.state = "failed"
        login.message = _failure_message(login)


def login_status() -> dict:
    login = _login
    if login is None:
        return {"state": "idle"}
    return {
        "state": login.state,
        "method": login.method,
        "url": login.url,
        "code": login.code,
        "message": login.message,
        "started_at": login.started_at,
    }


async def start_login(method: str) -> dict:
    """Run `grok login`; the UI shows its URL (and device code) and polls status."""
    global _login
    if _login is not None and _login.state == "running":
        return login_status()
    command, home = await grok_cli()
    if not command:
        raise GrokAccountError("Grok CLI not found. Install it or set its path in Agents.", 404)
    homes = await _auth_homes()
    paths = supergrok._auth_json_candidates(homes)
    primary = primary_auth_path(home)
    if primary not in paths:
        paths.append(primary)
    method = "device" if method == "device" else "oauth"
    proc = await asyncio.create_subprocess_exec(
        command,
        "login",
        "--device-auth" if method == "device" else "--oauth",
        env=_cli_env(home),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    login = GrokLogin(
        method=method, proc=proc, started_at=time.time(), before=_mtimes(paths), homes=homes
    )
    _login = login
    _spawn(_run_login(login))

    # Answer once the CLI prints its URL so the page can show it right away.
    deadline = time.monotonic() + 8
    while login.state == "running" and not login.url and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    return login_status()


async def submit_login_code(code: str) -> dict:
    """Paste the code some auth.x.ai pages show back into the running `grok login`."""
    login = _login
    if login is None or login.state != "running" or login.proc.stdin is None:
        raise GrokAccountError("No sign-in is in progress.", 409)
    login.proc.stdin.write(code.strip().encode() + b"\n")
    await login.proc.stdin.drain()
    return login_status()


def cancel_login() -> dict:
    login = _login
    if login is not None and login.state == "running":
        login.state = "cancelled"
        login.message = ""
        if login.proc.returncode is None:
            login.proc.kill()
    return login_status()


# ── Sign out ────────────────────────────────────────────────


async def logout() -> None:
    """`grok logout`, then remove every auth.json the agent or quota could read.

    The CLI can exit 0 while leaving an expired auth.json behind.
    """
    cancel_login()
    command, home = await grok_cli()
    if command:
        try:
            proc = await asyncio.create_subprocess_exec(
                command,
                "logout",
                env=_cli_env(home),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=LOGOUT_TIMEOUT_SECONDS)
        except (OSError, asyncio.TimeoutError) as error:
            log.info("grok logout failed (%s); removing auth.json anyway", error)
    paths = supergrok._auth_json_candidates(await _auth_homes())
    for path in [*paths, primary_auth_path(home)]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            log.warning("Could not remove %s: %s", path, error)
    await after_auth_change()


# ── Saved accounts ──────────────────────────────────────────


def _load_index() -> list[dict]:
    try:
        data = json.loads(ACCOUNTS_INDEX.read_text())
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _write_private(path: Path, content: bytes) -> None:
    """Atomically write a file only the server user can read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(content)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _save_index(items: list[dict]) -> None:
    _write_private(ACCOUNTS_INDEX, json.dumps(items, indent=2).encode())


def _snapshot_path(account_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", account_id):
        raise GrokAccountError("Unknown account.", 404)
    return ACCOUNTS_DIR / f"{account_id}.json"


def _account_key(profile: dict) -> str | None:
    return profile.get("user_id") or profile.get("email")


def list_saved_accounts(profile: dict) -> list[dict]:
    current = _account_key(profile) if profile.get("signed_in") else None
    return [
        {
            "id": item["id"],
            "label": item.get("label") or item.get("email") or "Grok",
            "email": item.get("email"),
            "saved_at": item.get("saved_at"),
            "active": bool(current) and item.get("key") == current,
        }
        for item in _load_index()
        if isinstance(item.get("id"), str)
    ]


def save_current_account(homes: list[str]) -> dict:
    """Snapshot the signed-in auth.json so the account can be switched back to."""
    profile, path, _entry = supergrok.best_auth(homes)
    if not profile["signed_in"] or path is None:
        raise GrokAccountError("Not signed in to Grok.", 409)
    try:
        content = path.read_bytes()
        json.loads(content)
    except (OSError, ValueError) as error:
        raise GrokAccountError(f"Could not read the Grok login: {error}") from error

    key = _account_key(profile)
    items = _load_index()
    item = next((entry for entry in items if key and entry.get("key") == key), None)
    if item is None:
        item = {"id": uuid.uuid4().hex[:12], "key": key}
        items.append(item)
    item.update(
        label=profile["display_name"] or profile["email"] or "Grok",
        email=profile["email"],
        saved_at=int(time.time()),
    )
    _write_private(_snapshot_path(item["id"]), content)
    _save_index(items)
    return item


async def switch_account(account_id: str) -> dict:
    snapshot = _snapshot_path(account_id)
    if not any(item.get("id") == account_id for item in _load_index()) or not snapshot.exists():
        raise GrokAccountError("Unknown account.", 404)
    if _login is not None and _login.state == "running":
        raise GrokAccountError("Finish or cancel the sign-in first.", 409)
    homes = await _auth_homes()
    # Keep the current login's refreshed tokens before replacing it.
    if supergrok.read_auth_profile(homes)["signed_in"]:
        try:
            save_current_account(homes)
        except GrokAccountError as error:
            log.info("Grok account snapshot skipped: %s", error)
    _command, home = await grok_cli()
    _write_private(primary_auth_path(home), snapshot.read_bytes())
    await after_auth_change()
    return supergrok.read_auth_profile(homes)


def remove_saved_account(account_id: str) -> None:
    snapshot = _snapshot_path(account_id)
    items = _load_index()
    remaining = [item for item in items if item.get("id") != account_id]
    if len(remaining) == len(items):
        raise GrokAccountError("Unknown account.", 404)
    snapshot.unlink(missing_ok=True)
    _save_index(remaining)


async def account_status() -> dict:
    homes = await _auth_homes()
    command, _home = await grok_cli()
    profile = supergrok.read_auth_profile(homes)
    return {
        "profile": profile,
        "cli_found": bool(command),
        "accounts": list_saved_accounts(profile),
        "login": login_status(),
    }
