"""Guards for response headers, audit events, passwords, setup tokens, and walks."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request

from cptr.env import configured_audit_level
from cptr.routers.auth import LoginRequest, login, new_password_ok
from cptr.utils import config
from cptr.utils.audit import (
    AuditLevel,
    agent_tool_subject,
    clear_tool_audit_seen,
    record_agent_tool,
    record_workspace_read,
)
from cptr.utils.runtime import FileError, _file_matches, _list_tree, walk_and_rank_files
from cptr.utils.security_headers import header_values

ROOT = Path(__file__).resolve().parents[1]


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "scheme": "http",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "query_string": b"",
            "server": ("127.0.0.1", 8000),
        }
    )


class ResponseHeaderTest(unittest.TestCase):
    def test_site_pages_get_the_policy_and_proxied_pages_do_not(self):
        page = header_values(path="/", content_type="text/html", disposition="", scheme="https")
        self.assertEqual(page["X-Content-Type-Options"], "nosniff")
        self.assertEqual(page["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(page["Referrer-Policy"], "no-referrer")
        self.assertIn("default-src 'self'", page["Content-Security-Policy"])
        self.assertIn("Strict-Transport-Security", page)

        proxied = header_values(
            path="/api/browser/frame",
            content_type="text/html",
            disposition="",
            scheme="http",
        )
        self.assertEqual(proxied["Content-Security-Policy"], "frame-ancestors 'self'")
        self.assertNotIn("default-src", proxied["Content-Security-Policy"])
        self.assertNotIn("Strict-Transport-Security", proxied)


class AuditEventTest(unittest.TestCase):
    def test_default_level_is_metadata(self):
        self.assertEqual(configured_audit_level({}), "METADATA")

    def test_command_and_file_read_are_recorded_without_their_contents(self):
        clear_tool_audit_seen()
        subject = agent_tool_subject("run_command", {"command": "ls /tmp", "output": "secret-output"})
        self.assertEqual(subject["tool"], "run_command")
        self.assertEqual(subject["detail"], "ls /tmp")
        self.assertNotIn("secret-output", subject["detail"])

        read = agent_tool_subject("read_file", {"path": "/home/ubuntu/.cptr/config.toml"})
        self.assertEqual(read["detail"], "/home/ubuntu/.cptr/config.toml")

        emitted = []
        with patch("cptr.utils.audit.get_audit_level", return_value=AuditLevel.METADATA), patch(
            "cptr.utils.audit._emit", side_effect=lambda entry: emitted.append(entry)
        ):
            record_agent_tool(
                user={"id": "u", "username": "admin"},
                session="chat-1",
                call_id="call-1",
                name="run_command",
                arguments={"command": "echo hello"},
                status="running",
            )
            record_workspace_read(_request("/api/workspace/files/read"), "/etc/hostname", "ok")
        self.assertEqual([item["event"] for item in emitted], ["agent_tool", "workspace_read"])
        self.assertEqual(emitted[1]["detail"], "/etc/hostname")
        self.assertNotIn("content", emitted[1])


class PasswordPolicyTest(unittest.IsolatedAsyncioTestCase):
    def test_eleven_characters_are_rejected_for_a_new_password(self):
        self.assertFalse(new_password_ok("12345678901"))
        self.assertFalse(new_password_ok("  12345678901  "))
        self.assertTrue(new_password_ok("123456789012"))

    def test_signup_stops_after_five_tries_from_one_source(self):
        config.clear_auth_limits()
        for _ in range(5):
            self.assertTrue(config.allow_signup("203.0.113.8"))
        self.assertFalse(config.allow_signup("203.0.113.8"))
        self.assertTrue(config.allow_signup("203.0.113.9"))

    async def test_right_password_still_passes_after_repeated_failures(self):
        config.clear_auth_limits()
        for _ in range(8):
            config.note_failure("login:admin")
        slept = []

        async def fake_sleep(delay):
            slept.append(delay)

        auth_row = SimpleNamespace(
            password="hash", user_id="u1", username="admin", totp_enabled=False
        )
        user_row = SimpleNamespace(role="admin")

        async def get_with_user(_name):
            return auth_row, user_row

        with (
            patch("cptr.routers.auth.get_auth_mode", return_value=config.AuthMode.PASSWORD),
            patch("cptr.routers.auth.Auth.get_with_user", get_with_user),
            patch("cptr.routers.auth.verify_password", return_value=True),
            patch("cptr.routers.auth.create_token", return_value="jwt"),
            patch("cptr.routers.auth.asyncio.sleep", fake_sleep),
        ):
            response = await login(
                _request("/api/auth/login"),
                LoginRequest(username="admin", password="short-but-existing"),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(slept, [])


class StartupTokenTest(unittest.TestCase):
    def test_headless_run_does_not_print_the_token(self):
        from click.testing import CliRunner

        from cptr.cli import cli

        token = "abc123token"
        data = tempfile.mkdtemp()
        previous = os.environ.get("CPTR_DATA_DIR")
        os.environ["CPTR_DATA_DIR"] = data
        try:
            with (
                patch("secrets.token_hex", return_value=token),
                patch("cptr.utils.startup_token.write_startup_token") as saved,
                patch("cptr.cli.uvicorn.Server") as server,
                patch("cptr.utils.updater.register_server"),
                patch("cptr.utils.updater.restart_if_requested"),
            ):
                server.return_value.run.return_value = None
                server.return_value.started = True
                result = CliRunner().invoke(cli, ["run", "--headless", "--port", "8765"])
        finally:
            if previous is None:
                os.environ.pop("CPTR_DATA_DIR", None)
            else:
                os.environ["CPTR_DATA_DIR"] = previous
            os.environ.pop("CPTR_STARTUP_TOKEN", None)
            os.environ.pop("CPTR_PORT", None)
        self.assertEqual(result.exit_code, 0, result.output + str(result.exception))
        self.assertNotIn(token, result.output)
        self.assertNotIn("?token=", result.output)
        saved.assert_called_once_with(token)


class WorkspaceWalkTest(unittest.TestCase):
    def test_root_search_is_refused_and_listing_does_not_count_children(self):
        with self.assertRaises(FileError):
            _file_matches("/", "passwd")
        with self.assertRaises(FileError):
            walk_and_rank_files("/", "a")
        listing = _list_tree("/")["text"]
        self.assertNotIn(" files)", listing)
        self.assertNotIn("\nproc/", "\n" + listing)


class MarkdownLinkTest(unittest.TestCase):
    def test_javascript_links_are_not_clickable(self):
        source = (ROOT / "cptr/frontend/src/lib/components/markdown/InlineRenderer.svelte").read_text()
        self.assertIn("linkHref", source)
        self.assertNotIn("href={href || '#'}", source)
        script = """
            import { linkHref, imageSrc } from './cptr/frontend/src/lib/markdownLinks.ts';
            const bad = ['javascript:alert(1)', 'data:text/html,hi', 'JaVaScRiPt:alert(1)'];
            for (const href of bad) {
              if (linkHref(href) !== null) throw new Error(href);
            }
            if (linkHref('https://example.com') !== 'https://example.com') throw new Error('https');
            if (imageSrc('javascript:alert(1)') !== null) throw new Error('img');
            if (imageSrc('/icon-192.png') !== '/icon-192.png') throw new Error('relative');
            """
        completed = subprocess.run(
            ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


class InstallScriptTest(unittest.TestCase):
    def test_failed_update_restores_the_frontend_and_the_unit_is_confined(self):
        script = r"""
set -euo pipefail
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
git -C "$root" init -q
git -C "$root" config user.email test@example.com
git -C "$root" config user.name test
printf 'build/\ncptr/frontend/build*\n' > "$root/.gitignore"
printf 'v1\n' > "$root/VERSION"
git -C "$root" add .gitignore VERSION
git -C "$root" commit -qm old
old=$(git -C "$root" rev-parse HEAD)
printf 'v2\n' > "$root/VERSION"
git -C "$root" commit -qam newer
mkdir -p "$root/cptr/frontend/build.old"
printf 'old-frontend\n' > "$root/cptr/frontend/build.old/index.html"
source "$1"
INSTALL_DIR="$root"
OLD_HEAD="$old"
FRONTEND_SWAPPED=1
BUILD_LOG=""
OS=Linux
NODE_BIN=/usr/bin
UV=/usr/bin/uv
HOST=127.0.0.1
PORT=8000
restore_failed_update
test -f "$root/cptr/frontend/build/index.html"
test ! -d "$root/cptr/frontend/build.old"
test "$(git -C "$root" rev-parse HEAD)" = "$old"
test "$(cat "$root/VERSION")" = "v1"
unit=$(write_systemd_unit system)
printf '%s\n' "$unit" | grep -q 'NoNewPrivileges=yes'
printf '%s\n' "$unit" | grep -q 'ProtectKernelTunables=yes'
if printf '%s\n' "$unit" | grep -q 'ProtectHome='; then
  echo 'ProtectHome must stay off' >&2
  exit 1
fi
"""
        completed = subprocess.run(
            ["bash", "-c", script, "bash", str(ROOT / "install.sh")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
