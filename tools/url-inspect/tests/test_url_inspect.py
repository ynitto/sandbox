import json
from pathlib import Path
import subprocess
import sys
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "url_inspect.py"


class UrlInspectTests(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True, text=True, encoding="utf-8",
        )

    def output(self, *args):
        result = self.invoke(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_components_userinfo_and_default_port(self):
        url = "https://user:pass@Example.com/path#section"
        output = self.output(url)
        self.assertIn("port: 443 (既定)", output)
        data = json.loads(self.output("--json", url))
        self.assertEqual(data["components"], {
            "scheme": "https", "username": "user", "password": "pass",
            "host": "example.com", "port": 443, "port_is_default": True,
            "path": "/path", "fragment": "section",
        })

    def test_ipv6_and_explicit_port(self):
        data = json.loads(self.output("--json", "http://[::1]:8080/"))
        self.assertEqual(data["components"]["host"], "::1")
        self.assertEqual(data["components"]["port"], 8080)
        self.assertFalse(data["components"]["port_is_default"])

    def test_escaped_userinfo_and_rebuilt_url_spelling(self):
        url = "HTTPS://user%2Fname@Example.com/path#section"
        output = self.output("--add", "a=1", url)
        self.assertIn("username: user%2Fname\n", output)
        self.assertIn("host: example.com\n", output)
        self.assertIn("rebuilt: HTTPS://user%2Fname@Example.com/path?a=1#section\n", output)

    def test_port_defaults(self):
        for url, host, port, is_default in (
            ("ws://example.com/", "example.com", 80, True),
            ("wss://example.com/", "example.com", 443, True),
            ("ftp://example.com/", "example.com", 21, True),
            ("http://[::1]/", "::1", 80, True),
            ("http://[::1]:80/", "::1", 80, False),
        ):
            with self.subTest(url=url):
                components = json.loads(self.output("--json", url))["components"]
                self.assertEqual(components["host"], host)
                self.assertEqual(components["port"], port)
                self.assertIs(components["port_is_default"], is_default)

    def test_missing_port_without_default(self):
        url = "ssh://example.com/"
        self.assertNotIn("port:", self.output(url))
        components = json.loads(self.output("--json", url))["components"]
        self.assertIsNone(components["port"])
        self.assertIs(components["port_is_default"], False)

    def test_ordered_query_decoding(self):
        url = "https://example.com/?%61=x+y&a=x%20z&flag&empty="
        data = json.loads(self.output("--json", url))
        self.assertEqual(data["params"], [
            {"key": "a", "value": "x y"}, {"key": "a", "value": "x z"},
            {"key": "flag", "value": None}, {"key": "empty", "value": ""},
        ])
        output = self.output(url)
        self.assertIn("'flag' (値なし)", output)
        self.assertIn("'empty' = ''", output)

    def test_edits(self):
        url = "https://example.com/?a=1&b=2&a=3&b=4&flag&empty=#top"
        options = ["--add", "a=last", "--set", "a=new value", "--set", "b=/",
                   "--set", "c=é", "--drop", "a"]
        expected = "https://example.com/?b=%2F&flag&empty=&a=new+value&c=%C3%A9&a=last#top"
        self.assertIn(f"rebuilt: {expected}\n", self.output(*options, url))
        self.assertEqual(json.loads(self.output("--json", *options, url))["rebuilt"], expected)

    def test_string_modes(self):
        self.assertEqual(self.output("--decode", "a+b%20c%2F"), "a+b c/\n")
        self.assertEqual(self.output("--encode", "a/b c+"), "a%2Fb%20c%2B\n")
        self.assertEqual(self.output("--encode", "a/b c", "--safe", "/"), "a/b%20c\n")

    def test_drop_decodes_key(self):
        url = "https://example.com/?a+b=1&a%20b=2&a%2Bb=3"
        data = json.loads(self.output("--json", "--drop", "a+b", url))
        self.assertEqual(data["rebuilt"], "https://example.com/?a%2Bb=3")

    def test_set_and_add_decode_keys_and_preserve_values(self):
        url = "https://example.com/?a+b=1&keep=2"
        data = json.loads(self.output("--json", "--set", "a%20b=x+y%2F",
                                     "--set", "new+key=3", "--add", "c%2Bd=4", url))
        self.assertEqual(data["rebuilt"],
                         "https://example.com/?a+b=x%2By%252F&keep=2&new+key=3&c%2Bd=4")

    def test_decode_invalid_percent_escape(self):
        result = self.invoke("--decode", "a+b%20c%")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "a+b%20c%\n")
        self.assertIn("warning:", result.stderr)

    def test_bad_percent_encoding(self):
        result = self.invoke("--json", "https://example.com/?a=%GG%20&b=%FF")
        self.assertEqual(result.returncode, 0)
        self.assertEqual([param["value"] for param in json.loads(result.stdout)["params"]],
                         ["%GG%20", "%FF"])
        self.assertEqual(result.stderr.count("warning:"), 2)

    def test_fix_scheme(self):
        for url in ("example.com:8080/path", "//example.com:8080/path"):
            with self.subTest(url=url):
                self.assertIn("scheme: https", self.output("--fix-scheme", url))
                self.assertIn("port: 8080", self.output("--fix-scheme", url))

    def test_invalid_inputs(self):
        for args in [("example.com/path",), ("https:///path",), ("http://[::1",),
                     ("http://::1/",), ("--safe", "/"),
                     ("http://example.com:65536",), ("http://example.com:no",),
                     ("https://example.com", "--set", "key"), ("--decode", "x", "http://x")]:
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("error:", result.stderr)
