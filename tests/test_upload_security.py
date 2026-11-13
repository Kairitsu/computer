"""Guards for upload responses, trusted-header login, and logout."""

import asyncio
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request
from starlette.routing import Match

from cptr.app import app
from cptr.routers.auth import COOKIE_NAME, logout
from cptr.utils import config
from cptr.utils.documents import workbook_preview
from cptr.utils.uploads import upload_headers


FRONTEND = Path(__file__).resolve().parents[1] / "cptr" / "frontend" / "src"


def _request(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/logout",
            "scheme": "http",
            "headers": [(b"cookie", f"{COOKIE_NAME}={token}".encode())],
            "client": ("127.0.0.1", 1234),
            "query_string": b"",
            "server": ("127.0.0.1", 8000),
        }
    )


class UploadHeadersTest(unittest.TestCase):
    def test_png_is_inline_and_not_cached(self):
        media, headers = upload_headers(b"\x89PNG\r\n\x1a\nrest", "photo.png")
        self.assertEqual(media, "image/png")
        self.assertTrue(headers["Content-Disposition"].startswith("inline;"))
        self.assertEqual(headers["Cache-Control"], "private, no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_html_is_an_opaque_attachment_even_with_an_html_name(self):
        media, headers = upload_headers(b"<html><script>alert(1)</script></html>", "page.html")
        self.assertEqual(media, "application/octet-stream")
        self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
        self.assertNotIn("text/html", media)
        self.assertEqual(headers["Cache-Control"], "private, no-store")

    def test_filename_cannot_split_the_header(self):
        _, headers = upload_headers(b"not-an-image", 'evil.html";\r\nX-Injected: 1')
        disposition = headers["Content-Disposition"]
        self.assertNotIn("\r", disposition)
        self.assertNotIn("\n", disposition)
        self.assertIn('filename="evil.html;X-Injected: 1"', disposition)


class TrustedHeaderTest(unittest.TestCase):
    def setUp(self):
        self.previous = config._config_cache

    def tearDown(self):
        config._config_cache = self.previous

    def _mode(self, sources):
        auth = {"mode": "trusted_header"}
        if sources is not None:
            auth["trusted_sources"] = sources
        config._config_cache = {"auth": auth}

    def test_empty_allowlist_ignores_the_header(self):
        for sources in ([], None, "127.0.0.1"):
            self._mode(sources)
            result = config.check_access("127.0.0.1", None, "admin")
            self.assertIsNone(result)

    def test_listed_client_is_that_username(self):
        self._mode(["10.0.0.8"])
        result = config.check_access("10.0.0.8", None, "ada")
        self.assertIsNotNone(result)
        self.assertEqual(result.username, "ada")
        self.assertIsNone(result.user_id)

    def test_unlisted_client_is_rejected(self):
        self._mode(["10.0.0.8"])
        self.assertIsNone(config.check_access("10.0.0.9", None, "ada"))


class LogoutTest(unittest.TestCase):
    def test_logout_makes_the_old_token_fail(self):
        user_id = "logout-test-user"
        token = config.create_token(user_id, "logout-test", "user")
        self.assertIsNotNone(config.verify_token(token))

        async def fake_revoke(uid: str):
            config._sessions_valid_after[uid] = time.time() + 1

        async def run():
            with patch("cptr.routers.auth.revoke_sessions", fake_revoke):
                await logout(_request(token))

        try:
            asyncio.run(run())
            self.assertIsNone(config.verify_token(token))
        finally:
            config._sessions_valid_after.pop(user_id, None)


class SpreadsheetPreviewTest(unittest.TestCase):
    def test_xlsx_cells_stay_text(self):
        from openpyxl import Workbook

        book = Workbook()
        sheet = book.active
        sheet.title = "Data"
        sheet["A1"] = "name"
        sheet["B1"] = "<script>"
        raw = BytesIO()
        book.save(raw)
        preview = workbook_preview(raw.getvalue(), "book.xlsx")
        self.assertEqual(preview["sheets"][0]["name"], "Data")
        self.assertEqual(preview["sheets"][0]["rows"][0][1], "<script>")
        self.assertFalse(preview["truncated"])


class RouteAndFrontendTest(unittest.TestCase):
    def test_sheets_route_matches_before_the_file_catch_all(self):
        scope = {"type": "http", "path": "/api/files/abc.xlsx/sheets", "method": "GET"}
        matched = None
        for route in app.router.routes:
            match, _child = route.matches(scope)
            if match == Match.FULL:
                matched = getattr(route, "path", None)
                break
        self.assertEqual(matched, "/api/files/{file_id_ext}/sheets")

    def test_previews_do_not_grant_same_origin_or_parse_xlsx(self):
        html = (FRONTEND / "lib/components/preview/HtmlPreview.svelte").read_text()
        modal = (FRONTEND / "lib/components/chat/UploadPreviewModal.svelte").read_text()
        office = (FRONTEND / "lib/components/preview/OfficePreview.svelte").read_text()
        self.assertIn('sandbox=""', html)
        self.assertIn('sandbox=""', modal)
        self.assertNotIn("allow-same-origin", html)
        self.assertNotIn("allow-same-origin", modal)
        self.assertNotIn("allow-scripts", html)
        self.assertNotIn("allow-scripts", modal)
        self.assertNotIn("import('xlsx')", office)
        self.assertNotIn("/api/workspace/files/serve/", html)


if __name__ == "__main__":
    unittest.main()
