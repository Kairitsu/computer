"""Check and install updates for the Grok and CodeBuddy command-line agents.

Checking never installs. Grok reports its own latest version with
`grok update --check --json`. CodeBuddy's `update` command installs as soon
as it finds a newer release, so the check uses `npm view` instead and only
runs `codebuddy update` after the user confirms.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import httpx

from cptr.env import DATA_DIR
from cptr.utils.agents.models import (
    default_agent_profiles,
    get_raw_agent_profiles,
    normalize_agent_profiles,
)

AGENT_ORDER = ("grok", "codebuddy")
DISPLAY_NAMES = {"grok": "Grok", "codebuddy": "CodeBuddy"}
CODEBUDDY_PACKAGE = "@tencent-ai/codebuddy-code"

CHECK_TIMEOUT = 40
INSTALL_TIMEOUT = 10 * 60
NOTE_LIMIT = 24_000

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)")
_VERSION_EXACT = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")
_GROK_HEADING = re.compile(r"^#\s+(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\b.*$", re.MULTILINE)
_CODEBUDDY_HEADING = re.compile(r"^##\s+\[([^\]]+)\]", re.MULTILINE)

_install_lock = asyncio.Lock()


class AgentUpdateError(Exception):
    """A check or install problem the settings page can explain."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def version_tuple(value: str) -> tuple[tuple[int, int, int], str | None] | None:
    """Parse a dotted version. A release sorts above a pre-release of the same numbers."""
    match = _VERSION_EXACT.fullmatch(value.strip())
    if not match:
        return None
    numbers = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return numbers, match.group(4)


def version_gt(left: str, right: str) -> bool:
    """True when `left` is a newer semver than `right`."""
    parsed_left = version_tuple(left)
    parsed_right = version_tuple(right)
    if parsed_left is None or parsed_right is None:
        return False
    if parsed_left[0] != parsed_right[0]:
        return parsed_left[0] > parsed_right[0]
    if parsed_left[1] is None and parsed_right[1] is not None:
        return True
    if parsed_left[1] is not None and parsed_right[1] is None:
        return False
    if parsed_left[1] is None or parsed_right[1] is None:
        return False
    return parsed_left[1] > parsed_right[1]


def find_version(text: str) -> str | None:
    """The last dotted version in command output. Warnings earlier in the text are ignored."""
    found = None
    for line in text.splitlines() or [text]:
        match = _VERSION_RE.search(line.strip().strip('"').strip("'"))
        if match and version_tuple(match.group(1)):
            found = match.group(1)
    return found


def slice_version_notes(text: str, pattern: re.Pattern[str], current: str, latest: str) -> str:
    """Keep headings whose version is newer than `current` and not newer than `latest`."""
    matches = list(pattern.finditer(text))
    if not matches:
        return ""
    chunks: list[str] = []
    for index, match in enumerate(matches):
        version = match.group(1).strip()
        if version_tuple(version) is None:
            continue
        if not version_gt(version, current) or version_gt(version, latest):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        piece = text[match.start() : end].strip()
        if piece:
            chunks.append(piece)
    notes = "\n\n".join(chunks)
    if len(notes) > NOTE_LIMIT:
        return notes[:NOTE_LIMIT].rstrip() + "\n\n…"
    return notes


def format_grok_json_notes(payload: Any, version: str) -> str:
    """Turn Grok's changelog JSON (category, description) into a short markdown note."""
    if not isinstance(payload, list):
        return ""
    lines = [f"# {version}", ""]
    for item in payload:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "").strip()
        if not description:
            continue
        category = str(item.get("category") or "").strip()
        prefix = f"**{category}.** " if category else ""
        lines.append(f"- {prefix}{description}")
    if len(lines) == 2:
        return ""
    return "\n".join(lines)


def _profile_env(profile: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    home = profile.get("home")
    if isinstance(home, str) and home.strip():
        env["HOME"] = os.path.expanduser(home.strip())
    home_dir = env.get("HOME") or str(Path.home())
    local_bin = str(Path(home_dir) / ".local" / "bin")
    current = env.get("PATH") or ""
    parts = current.split(os.pathsep) if current else []
    if local_bin not in parts:
        env["PATH"] = os.pathsep.join([local_bin, *parts]) if parts else local_bin
    return env


def _resolve_command(command: str, env: dict[str, str]) -> str | None:
    command = command.strip()
    if not command:
        return None
    expanded = os.path.expanduser(command)
    if os.path.isabs(expanded):
        return expanded if os.access(expanded, os.X_OK) else None
    return shutil.which(command, path=env.get("PATH"))


def _grok_changelog_path(env: dict[str, str]) -> Path:
    home = Path(env.get("HOME") or str(Path.home()))
    return home / ".grok" / "CHANGELOG.md"


def _notes_cache(agent: str, version: str) -> Path:
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", version)
    return DATA_DIR / "agent-update-notes" / f"{agent}-{safe}.md"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def _write_text(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        return


async def _profiles_for_update() -> dict[str, dict[str, Any]]:
    raw = await get_raw_agent_profiles()
    profiles = default_agent_profiles() if raw is None else normalize_agent_profiles(raw)
    chosen: dict[str, dict[str, Any]] = {}
    for agent in AGENT_ORDER:
        matches = [profile for profile in profiles if profile.get("agent") == agent]
        enabled = [profile for profile in matches if profile.get("mode") != "disabled"]
        pick = enabled or matches
        if pick:
            chosen[agent] = pick[0]
    return chosen


async def _run(argv: list[str], env: dict[str, str], timeout: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        with_timeout = "timed out"
        try:
            await proc.wait()
        except ProcessLookupError:
            pass
        return 124, "", with_timeout
    out = (stdout or b"").decode(errors="replace").strip()
    err = (stderr or b"").decode(errors="replace").strip()
    return proc.returncode or 0, out, err


def _clip(text: str, limit: int = 2000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _result(
    agent: str,
    *,
    name: str | None = None,
    command: str | None = None,
    current_version: str | None = None,
    latest_version: str | None = None,
    update_available: bool = False,
    can_update: bool = False,
    channel: str | None = None,
    notes: str = "",
    error: str | None = None,
    detail: str = "",
) -> dict[str, Any]:
    return {
        "agent": agent,
        "name": name or DISPLAY_NAMES[agent],
        "command": command,
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
        "can_update": can_update,
        "channel": channel,
        "notes": notes,
        "notes_available": bool(notes.strip()),
        "error": error,
        "detail": detail,
    }


def _json_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


async def _fetch_text(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "cptr"})
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    text = response.text
    if not text or text.lstrip().startswith("<"):
        return None
    return text


def _grok_note_urls(version: str, channel: str | None) -> list[str]:
    """Candidates observed around the CLI's `https://x.ai/cli/changelogs` prefix."""
    channel = channel or "stable"
    names = [
        f"https://x.ai/cli/changelogs/{version}.md",
        f"https://x.ai/cli/changelogs/{version}.json",
        f"https://x.ai/cli/changelogs/{version}",
        f"https://x.ai/cli/changelogs/{channel}/{version}.json",
        f"https://x.ai/cli/changelogs/{channel}/{version}.md",
        f"https://storage.googleapis.com/grok-build-public-artifacts/cli/changelogs/{version}.md",
        f"https://storage.googleapis.com/grok-build-public-artifacts/cli/changelogs/{version}.json",
    ]
    return names


async def _grok_notes(env: dict[str, str], current: str, latest: str, channel: str | None) -> str:
    local = slice_version_notes(
        _read_text(_grok_changelog_path(env)), _GROK_HEADING, current, latest
    )
    if local:
        return local
    cached = _read_text(_notes_cache("grok", latest))
    if cached:
        return cached
    for url in _grok_note_urls(latest, channel):
        body = await _fetch_text(url)
        if not body:
            continue
        if body.lstrip().startswith("{") or body.lstrip().startswith("["):
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                continue
            notes = format_grok_json_notes(payload, latest)
        else:
            notes = slice_version_notes(body, _GROK_HEADING, current, latest)
            if not notes and latest in body:
                notes = body.strip()
        if notes:
            if len(notes) > NOTE_LIMIT:
                notes = notes[:NOTE_LIMIT].rstrip() + "\n\n…"
            _write_text(_notes_cache("grok", latest), notes)
            return notes
    return ""


def _is_codebuddy_package(command: str) -> bool:
    real = Path(os.path.realpath(command))
    for parent in [real.parent, *real.parents]:
        package_file = parent / "package.json"
        if not package_file.is_file():
            if parent.parent == parent or len(parent.parts) <= 2:
                break
            continue
        try:
            data = json.loads(package_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        return data.get("name") == CODEBUDDY_PACKAGE
    return False


async def _codebuddy_notes(current: str, latest: str) -> str:
    cached = _read_text(_notes_cache("codebuddy", latest))
    if cached:
        sliced = slice_version_notes(cached, _CODEBUDDY_HEADING, current, latest)
        if sliced:
            return sliced
    urls = [
        f"https://unpkg.com/{CODEBUDDY_PACKAGE}@{latest}/CHANGELOG.md",
        f"https://cdn.jsdelivr.net/npm/{CODEBUDDY_PACKAGE}@{latest}/CHANGELOG.md",
    ]
    for url in urls:
        body = await _fetch_text(url)
        if not body or "## [" not in body:
            continue
        notes = slice_version_notes(body, _CODEBUDDY_HEADING, current, latest)
        if notes:
            _write_text(_notes_cache("codebuddy", latest), body)
            return notes
    packed = await _codebuddy_changelog_from_npm(latest)
    if not packed:
        return ""
    notes = slice_version_notes(packed, _CODEBUDDY_HEADING, current, latest)
    if notes:
        _write_text(_notes_cache("codebuddy", latest), packed)
    return notes


async def _codebuddy_changelog_from_npm(version: str) -> str:
    """Last resort: the changelog sits inside the published package tarball."""
    with tempfile.TemporaryDirectory(prefix="cptr-codebuddy-notes-") as tmp:
        code, out, err = await _run(
            ["npm", "pack", f"{CODEBUDDY_PACKAGE}@{version}", "--pack-destination", tmp],
            os.environ.copy(),
            180,
        )
        if code != 0:
            return ""
        archives = list(Path(tmp).glob("*.tgz"))
        if not archives:
            return ""
        try:
            with tarfile.open(archives[0], "r:gz") as bundle:
                member = bundle.getmember("package/CHANGELOG.md")
                extracted = bundle.extractfile(member)
                if extracted is None:
                    return ""
                return extracted.read().decode("utf-8", errors="replace")
        except (KeyError, tarfile.TarError, OSError):
            _ = err, out
            return ""


async def _check_grok(profile: dict[str, Any]) -> dict[str, Any]:
    env = _profile_env(profile)
    name = str(profile.get("name") or DISPLAY_NAMES["grok"])
    command = _resolve_command(str(profile.get("command") or "grok"), env)
    if command is None:
        return _result("grok", name=name, error="not_found")
    code, out, err = await _run([command, "update", "--check", "--json"], env, CHECK_TIMEOUT)
    payload = _json_object(out or err)
    if code != 0 or payload is None:
        return _result(
            "grok",
            name=name,
            command=command,
            error="check_failed",
            detail=_clip(err or out),
        )
    if payload.get("error"):
        return _result(
            "grok",
            name=name,
            command=command,
            error="check_failed",
            detail=_clip(str(payload.get("error"))),
            channel=str(payload.get("channel") or "") or None,
        )
    current = str(payload.get("currentVersion") or "") or None
    latest = str(payload.get("latestVersion") or "") or None
    available = bool(payload.get("updateAvailable"))
    if current and latest and version_gt(latest, current):
        available = True
    if current and latest and not version_gt(latest, current):
        available = False
    channel = str(payload.get("channel") or "") or None
    notes = ""
    if available and current and latest:
        notes = await _grok_notes(env, current, latest, channel)
    return _result(
        "grok",
        name=name,
        command=command,
        current_version=current,
        latest_version=latest,
        update_available=available,
        can_update=available,
        channel=channel,
        notes=notes,
    )


async def _check_codebuddy(profile: dict[str, Any]) -> dict[str, Any]:
    env = _profile_env(profile)
    name = str(profile.get("name") or DISPLAY_NAMES["codebuddy"])
    command = _resolve_command(str(profile.get("command") or "codebuddy"), env)
    if command is None:
        return _result("codebuddy", name=name, error="not_found")
    if not _is_codebuddy_package(command):
        return _result(
            "codebuddy",
            name=name,
            command=command,
            error="unsupported",
            detail="This CodeBuddy command is not the official npm package.",
        )
    code, out, err = await _run([command, "--version"], env, CHECK_TIMEOUT)
    current = find_version(out)
    if code != 0 or not current:
        return _result(
            "codebuddy",
            name=name,
            command=command,
            error="check_failed",
            detail=_clip(err or out),
        )
    code, out, err = await _run(["npm", "view", CODEBUDDY_PACKAGE, "version"], env, CHECK_TIMEOUT)
    latest = find_version(out)
    if code != 0 or not latest:
        return _result(
            "codebuddy",
            name=name,
            command=command,
            current_version=current,
            error="check_failed",
            detail=_clip(err or out),
        )
    available = version_gt(latest, current)
    notes = await _codebuddy_notes(current, latest) if available else ""
    return _result(
        "codebuddy",
        name=name,
        command=command,
        current_version=current,
        latest_version=latest,
        update_available=available,
        can_update=available,
        notes=notes,
    )


async def check_agent_updates() -> dict[str, Any]:
    """Check Grok and CodeBuddy. Does not install anything."""
    profiles = await _profiles_for_update()
    checkers = {"grok": _check_grok, "codebuddy": _check_codebuddy}
    agents = [agent for agent in AGENT_ORDER if agent in profiles]
    results = await asyncio.gather(*(checkers[agent](profiles[agent]) for agent in agents))
    return {"agents": list(results)}


async def _install(agent: str, profile: dict[str, Any]) -> dict[str, Any]:
    env = _profile_env(profile)
    command_name = "grok" if agent == "grok" else "codebuddy"
    command = _resolve_command(str(profile.get("command") or command_name), env)
    if command is None:
        raise AgentUpdateError("not_found")
    if agent == "codebuddy" and not _is_codebuddy_package(command):
        raise AgentUpdateError(
            "unsupported", "This CodeBuddy command is not the official npm package."
        )
    before = ""
    if agent == "grok":
        code, out, err = await _run([command, "update", "--check", "--json"], env, CHECK_TIMEOUT)
        payload = _json_object(out or err)
        if payload:
            before = str(payload.get("currentVersion") or "")
    else:
        code, out, _err = await _run([command, "--version"], env, CHECK_TIMEOUT)
        before = find_version(out) or ""
    code, out, err = await _run([command, "update"], env, INSTALL_TIMEOUT)
    log = _clip("\n".join(part for part in (out, err) if part))
    if code != 0:
        raise AgentUpdateError("install_failed", log)
    if agent == "grok":
        _code, out, err = await _run([command, "update", "--check", "--json"], env, CHECK_TIMEOUT)
        payload = _json_object(out or err) or {}
        version = str(payload.get("currentVersion") or "")
    else:
        _code, out, _err = await _run([command, "--version"], env, CHECK_TIMEOUT)
        version = find_version(out) or ""
    if not version:
        version = before
    if before and version and not version_gt(version, before):
        raise AgentUpdateError("install_failed", log or "version did not change")
    return {
        "agent": agent,
        "ok": True,
        "version": version,
        "previous_version": before or None,
        "error": None,
        "detail": "",
        "log": log,
    }


async def apply_agent_update(agent: str) -> dict[str, Any]:
    """Install the latest version of one agent. One install runs at a time."""
    if agent not in AGENT_ORDER:
        raise AgentUpdateError("unsupported", "agent must be grok or codebuddy")
    profiles = await _profiles_for_update()
    profile = profiles.get(agent)
    if profile is None:
        raise AgentUpdateError("not_found")
    async with _install_lock:
        return await _install(agent, profile)
