"""変えたあとに影響を測る名前（実際の差分から拾う名前）を、代表的な差分で固定する。"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MACHINE = Path(__file__).resolve().parents[1] / "machine"


def load_codd():
    spec = importlib.util.spec_from_file_location("codd_machine", MACHINE / "codd.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # dataclass が自分のモジュールを引けるように
    spec.loader.exec_module(mod)
    return mod


codd = load_codd()

ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", "PATH": "/usr/bin:/bin"}

BEFORE = """\
export function DesktopPreviewWorkspace(props) {
  const disabled = !canConfirm(props.items);
  const handleNext = () => props.onNext();
  return <PrintButton disabled={disabled} onNext={handleNext} />;
}
"""

AFTER = """\
export function DesktopPreviewWorkspace(props) {
  const isGeneratedPrintDisabled = !props.generated;
  const disabled = isGeneratedPrintDisabled;
  return <PrintButton disabled={disabled} />;
}
"""


class TermsFromDiffTest(unittest.TestCase):
    def test_locals_whose_value_changed_are_not_names_that_moved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=ENV)
            git("init", "-q", "-b", "main")
            (repo / "view.tsx").write_text(BEFORE, encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "init")
            (repo / "view.tsx").write_text(AFTER, encoding="utf-8")
            terms = codd.terms_from_diff(codd.Side("own", repo, []))
        self.assertNotIn("disabled", terms)                  # 中身だけ変えた関数の中の変数
        self.assertIn("isGeneratedPrintDisabled", terms)     # 足した変数
        self.assertIn("handleNext", terms)                   # 消した変数

    def test_top_level_and_exported_values_are_still_names(self) -> None:
        # 字下げの無い・export した定数は外から指される名前なので、中身だけ変えても拾う。
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=ENV)
            git("init", "-q", "-b", "main")
            (repo / "limits.ts").write_text("export const MAX_ITEMS = 10;\nconst PAGE_SIZE = 20;\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "init")
            (repo / "limits.ts").write_text("export const MAX_ITEMS = 12;\nconst PAGE_SIZE = 30;\n", encoding="utf-8")
            terms = codd.terms_from_diff(codd.Side("own", repo, []))
        self.assertIn("MAX_ITEMS", terms)
        self.assertIn("PAGE_SIZE", terms)



class LiteralsFromDiffTest(unittest.TestCase):
    def test_text_between_jsx_tags_counts_as_a_changed_string(self) -> None:
        # React の画面の文言は引用符で囲まれない。e2e のケースはこの文言で書くので、変えたら拾う。
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=ENV)
            git("init", "-q", "-b", "main")
            form = "export function F() {\n  if (x > 1 && y < 2) {}\n  return <button type=\"submit\">{0}</button>;\n}}\n"
            (repo / "LoginForm.tsx").write_text(form.replace("{0}", "ログイン").replace("}}", "}"), encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "init")
            (repo / "LoginForm.tsx").write_text(form.replace("{0}", "送信").replace("}}", "}")
                                                .replace("x > 1", "x > 2"), encoding="utf-8")
            ctx = SimpleNamespace(config={"tests": ["**/*.test.*"]})
            texts = codd.literals_from_diff(ctx, "", codd.Side("own", repo, []))
        self.assertEqual(sorted(texts), ["ログイン", "送信"])   # 比較式（x > 2 && y < 2）は文言にしない

if __name__ == "__main__":
    unittest.main()
