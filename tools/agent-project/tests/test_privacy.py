"""共有前 redaction の契約テスト。"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403
from _privacy_fixture import PRIVACY_FIXTURE  # noqa: E402


class TestPrivacyRedactionContract(unittest.TestCase):
    def _payload(self, case: dict) -> str:
        safe = "\n".join(f"{key}={value}" for key, value in PRIVACY_FIXTURE["safe"].items())
        return f"{case['payload']}\n{safe}"

    def _assert_no_sensitive_value(self, text: str, case: dict) -> None:
        for value in case["forbidden"]:
            self.assertNotIn(value, text)

    def _assert_redacted(self, text: str, case: dict) -> None:
        self._assert_no_sensitive_value(text, case)
        self.assertIn(f"[REDACTED:{case['marker']}]", text)
        for value in PRIVACY_FIXTURE["safe"].values():
            self.assertIn(value, text)

    def test_each_fixture_case_detects_its_own_unredacted_leak(self):
        for name, case in PRIVACY_FIXTURE["cases"].items():
            with self.subTest(case=name), self.assertRaises(AssertionError):
                self._assert_no_sensitive_value(self._payload(case), case)

    def test_brief_and_decision_outputs_redact_secrets_and_preserve_safe_values(self):
        for name, case in PRIVACY_FIXTURE["cases"].items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cfg = cfg_for(root)
                task = km.Task(id="T1", title="privacy")
                payload = self._payload(case)

                self.assertTrue(km.append_brief_item(cfg, task, payload, source="fixture"))
                km.append_decision(cfg, task.id, "fixture", payload, "record", payload, task.id)

                brief = km.brief_path(cfg, task).read_text(encoding="utf-8")
                decision = km.decision_path(cfg, task.id).read_text(encoding="utf-8")
                self._assert_redacted(brief, case)
                self._assert_redacted(decision, case)

    def test_state_repository_rejects_fixture_before_commit_or_push(self):
        for name, case in PRIVACY_FIXTURE["cases"].items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                remote, root = tmp / "remote.git", tmp / "state"
                subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
                git_init(root)
                (root / "seed.md").write_text("safe\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(root), "add", "seed.md"], check=True)
                subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed"], check=True)
                subprocess.run(["git", "-C", str(root), "remote", "add", "origin", str(remote)],
                               check=True)
                subprocess.run(["git", "-C", str(root), "push", "-qu", "origin", "main"],
                               check=True)
                before = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                        check=True, capture_output=True, text=True).stdout.strip()
                (root / "leak.txt").write_text(self._payload(case), encoding="utf-8")

                with self.assertRaises(km.ShareSafetyError):
                    km.DirectStateGit(root, interval=0.0).sync(force=True)

                self.assertEqual(subprocess.run(
                    ["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                    capture_output=True, text=True).stdout.strip(), before)
                self.assertEqual(subprocess.run(
                    ["git", "-C", str(remote), "rev-parse", "refs/heads/main"], check=True,
                    capture_output=True, text=True).stdout.strip(), before)

    def test_residual_redaction_fails_closed_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = cfg_for(root)
            task = km.Task(id="T1", title="privacy")
            failure = km.ShareSafetyError("brief/T1.md", ("TOKEN",))
            case = PRIVACY_FIXTURE["cases"]["token_value"]

            with mock.patch.object(km, "assert_share_safe", side_effect=failure), \
                    self.assertRaises(km.ShareSafetyError):
                km.append_brief_item(cfg, task, self._payload(case), source="fixture")

            self.assertFalse(km.brief_path(cfg, task).exists())


if __name__ == "__main__":
    unittest.main()
