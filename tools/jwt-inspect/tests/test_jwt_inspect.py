import base64
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import hmac
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "jwt_inspect.py"
sys.path.insert(0, str(SCRIPT.parent))
from jwt_inspect import main


def encode_segment(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def make_token(payload, *, alg="HS256", secret=None):
    header = json.dumps({"typ": "JWT", "alg": alg}).encode("utf-8")
    body = json.dumps(payload).encode("utf-8")
    message = f"{encode_segment(header)}.{encode_segment(body)}"
    signature = b""
    if secret is not None:
        signature = hmac.new(secret.encode("utf-8"), message.encode("ascii"), hashlib.sha256).digest()
    return f"{message}.{encode_segment(signature)}"


class JwtInspectTests(unittest.TestCase):
    def run_main(self, *args, now=100):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(args, now=now)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_decode(self):
        token = make_token({"sub": "利用者", "aud": "example"}, secret="test")
        code, output, error = self.run_main(token)
        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertIn('Header:\n{\n  "typ": "JWT",\n  "alg": "HS256"\n}', output)
        self.assertIn('Payload:\n{\n  "sub": "利用者",\n  "aud": "example"\n}', output)
        self.assertIn("署名: あり (32 バイト)", output)
        self.assertNotIn("署名検証:", output)

    def test_time_claims(self):
        token = make_token({"exp": 100, "nbf": 101, "iat": 0, "auth_time": 1})
        code, output, error = self.run_main(token, "--tz", "Asia/Tokyo")
        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertIn("exp: 1970-01-01T09:01:40+09:00", output)
        self.assertIn("nbf: 1970-01-01T09:01:41+09:00", output)
        self.assertIn("iat: 1970-01-01T09:00:00+09:00", output)
        self.assertIn("auth_time: 1970-01-01T09:00:01+09:00", output)
        self.assertIn("期限切れ", output)
        self.assertIn("まだ有効になっていない", output)
        _, utc_output, _ = self.run_main(token, now=99)
        self.assertIn("exp: 1970-01-01T00:01:40+00:00", utc_output)
        self.assertNotIn("期限切れ", utc_output)

    def test_invalid_tokens(self):
        cases = {
            "one.two": "パート数",
            "@.e30.": "base64url",
            "e30.e30.++": "base64url",
            "e30.e30.YWJj=": "パディング",
            f"{encode_segment(b'not JSON')}.e30.": "Header",
            "e30.W10.": "オブジェクト",
        }
        for token, reason in cases.items():
            with self.subTest(token=token):
                code, output, error = self.run_main(token)
                self.assertEqual(code, 2)
                self.assertEqual(output, "")
                self.assertIn(reason, error)

    def test_hs256_verification(self):
        token = make_token({"sub": "user"}, secret="秘密")
        for secret, expected_code, result in (("秘密", 0, "OK"), ("wrong", 3, "NG")):
            with self.subTest(result=result):
                code, output, _ = self.run_main("--secret", secret, token)
                self.assertEqual(code, expected_code)
                self.assertIn(f"署名検証: {result}", output)

    def test_unsupported_algorithm(self):
        code, output, _ = self.run_main("--secret", "test", make_token({}, alg="none"))
        self.assertEqual(code, 3)
        self.assertIn("検証できません", output)

    def test_stdin(self):
        token = make_token({"sub": "stdin"})
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=" \n" + token.replace(".", ".\n\t") + " \n",
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Header:", result.stdout)
        self.assertIn('Payload:\n{\n  "sub": "stdin"', result.stdout)
        self.assertIn("署名: なし (0 バイト)", result.stdout)

    def test_file_and_input_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token.txt"
            path.write_text(make_token({"sub": "file"}) + "\n", encoding="utf-8")
            code, output, _ = self.run_main("--file", str(path))
            self.assertEqual(code, 0)
            self.assertIn('"sub": "file"', output)
            code, output, _ = self.run_main("--file", str(path), make_token({"sub": "argument"}))
            self.assertEqual(code, 0)
            self.assertIn('"sub": "argument"', output)
            code, _, error = self.run_main("--file", str(path.with_name("missing.txt")))
            self.assertEqual(code, 1)
            self.assertIn("エラー", error)


if __name__ == "__main__":
    unittest.main()
