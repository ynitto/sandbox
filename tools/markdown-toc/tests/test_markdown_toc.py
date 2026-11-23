import codecs
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CLI = Path(__file__).resolve().parents[1] / "markdown_toc.py"


class MarkdownTocTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "input.md"

    def run_cli(self, *args, stdin=None):
        target = "-" if stdin is not None else str(self.path)
        return subprocess.run(
            [sys.executable, str(CLI), target, *args],
            input=stdin,
            capture_output=True,
        )

    def test_generate_and_update(self):
        source = "# Title\n\n  <!-- toc --> \nold\n <!-- /toc -->\n\n## One\n### Two ###\n"
        self.path.write_text(source, encoding="utf-8")
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            source.replace("old\n", "- [One](#one)\n  - [Two](#two)\n"),
        )

    def test_ignore_fences_and_comments(self):
        source = (
            "````md\n```\n## Hidden\n````\n"
            "~~~\n<!-- toc -->\n## Hidden\n~~~\n"
            "<!--\n## Hidden\n<!-- toc -->\n"
            "<!-- toc -->\n<!-- /toc -->\n## Shown <!-- note -->\n"
        )
        result = self.run_cli(stdin=source.encode())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"- [Shown](#shown)", result.stdout)
        self.assertNotIn(b"- [Hidden]", result.stdout)

    def test_duplicate_and_japanese_anchors(self):
        source = "<!-- toc -->\n<!-- /toc -->\n## 日本語 Test_!?\n## 日本語 Test_!?\n## 日本語 Test_!?\n"
        result = self.run_cli(stdin=source.encode())
        self.assertEqual(result.returncode, 0)
        expected = "".join(
            f"- [日本語 Test_!?](#日本語-test_{suffix})\n"
            for suffix in ("", "-1", "-2")
        )
        self.assertEqual(result.stdout.decode(), source.replace("<!-- /toc -->", expected + "<!-- /toc -->"))

    def test_level_limits_and_stdout(self):
        source = b"<!-- toc -->\n<!-- /toc -->\n## Same\n### Same\n#### Four\n##### Five\n"
        self.path.write_bytes(source)
        result = self.run_cli("--min-level", "3", "--max-level", "4", "--stdout")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout,
            source.replace(b"<!-- /toc -->", b"- [Same](#same-1)\n  - [Four](#four)\n<!-- /toc -->"),
        )
        self.assertEqual(self.path.read_bytes(), source)

    def test_check(self):
        source = b"<!-- toc -->\n<!-- /toc -->\n## One\n"
        self.path.write_bytes(source)
        stale = self.run_cli("--check")
        self.assertEqual(stale.returncode, 1)
        self.assertIn(b"out of date", stale.stderr)
        self.assertEqual(self.path.read_bytes(), source)
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual(self.run_cli("--check").returncode, 0)

    def test_missing_markers_and_create(self):
        source = b"# Title\n## One\n"
        self.path.write_bytes(source)
        failure = self.run_cli()
        self.assertEqual(failure.returncode, 2)
        self.assertIn(b"markers are missing", failure.stderr)
        self.assertEqual(self.run_cli("--check").returncode, 1)
        self.assertEqual(self.path.read_bytes(), source)
        self.assertEqual(self.run_cli("--create").returncode, 0)
        self.assertEqual(
            self.path.read_bytes(),
            b"# Title\n\n<!-- toc -->\n- [One](#one)\n<!-- /toc -->\n\n## One\n",
        )
        self.assertEqual(self.run_cli("--check").returncode, 0)

    def test_create_at_end_of_stream(self):
        result = self.run_cli("--create", stdin=b"## Heading")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout, b"## Heading\n\n<!-- toc -->\n- [Heading](#heading)\n<!-- /toc -->\n"
        )

    def test_crlf_and_bom(self):
        source = codecs.BOM_UTF8 + b"<!-- toc -->\r\nold\r\n<!-- /toc -->\r\n## One"
        self.path.write_bytes(source)
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual(self.path.read_bytes(), source.replace(b"old", b"- [One](#one)"))

    def test_errors(self):
        cases = [
            (b"<!-- toc -->\n", (), b"closing marker"),
            (b"<!-- /toc -->\n", ("--create",), b"opening marker"),
            (b"Plain text", ("--create",), b"no ATX heading"),
            (b"\xff", (), b"utf-8"),
            (b"", ("--min-level", "5", "--max-level", "2"), b"must not exceed"),
        ]
        for source, args, message in cases:
            with self.subTest(source=source, args=args):
                result = self.run_cli(*args, stdin=source)
                self.assertEqual(result.returncode, 2)
                self.assertIn(message, result.stderr)
        self.assertEqual(self.run_cli().returncode, 2)
