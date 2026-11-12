import os
import tempfile
import unittest
from pathlib import Path

from cptr.app import resolve_frontend_file


class ResolveFrontendFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "build"
        self.root.mkdir()
        self.inside = self.root / "app.js"
        self.inside.write_text("ok", encoding="utf-8")
        nested = self.root / "assets"
        nested.mkdir()
        (nested / "icon.png").write_text("png", encoding="utf-8")
        self.outside = Path(self.tmp.name) / "secret.txt"
        self.outside.write_text("secret", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_file_inside_build_dir(self):
        self.assertEqual(resolve_frontend_file(self.root, "app.js"), self.inside.resolve())
        self.assertEqual(
            resolve_frontend_file(self.root, "assets/icon.png"),
            (self.root / "assets" / "icon.png").resolve(),
        )

    def test_parent_segments_that_stay_inside(self):
        self.assertEqual(
            resolve_frontend_file(self.root, "assets/../app.js"),
            self.inside.resolve(),
        )

    def test_parent_segments_that_escape(self):
        escaped = os.path.relpath(self.outside, self.root)
        self.assertIsNone(resolve_frontend_file(self.root, escaped))

    def test_absolute_path_replaces_root(self):
        self.assertIsNone(resolve_frontend_file(self.root, str(self.outside)))
        self.assertIsNone(resolve_frontend_file(self.root, "/" + str(self.outside).lstrip("/")))

    def test_symlink_outside_is_rejected(self):
        link = self.root / "leak"
        link.symlink_to(self.outside)
        self.assertIsNone(resolve_frontend_file(self.root, "leak"))

    def test_symlink_inside_is_allowed(self):
        link = self.root / "alias.js"
        link.symlink_to(self.inside)
        self.assertEqual(resolve_frontend_file(self.root, "alias.js"), self.inside.resolve())

    def test_missing_file_and_empty_path(self):
        self.assertIsNone(resolve_frontend_file(self.root, ""))
        self.assertIsNone(resolve_frontend_file(self.root, "missing.js"))
        self.assertIsNone(resolve_frontend_file(self.root, "assets"))


if __name__ == "__main__":
    unittest.main()
