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

    def test_backticks_in_code_are_not_names(self) -> None:
        # JS のテンプレート文字列・Go の構造体タグは名前ではない（上限の語数を使い切り、本当の名前を測り残す）。
        # 文書の `…` は、これまでどおり名前として拾う
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=ENV)
            git("init", "-q", "-b", "main")
            (repo / "view.js").write_text("export function label(t) {\n  return t.title;\n}\n", encoding="utf-8")
            (repo / "task.go").write_text("package task\n\ntype Task struct {\n\tTitle string `json:\"title\"`\n}\n",
                                          encoding="utf-8")
            (repo / "api.md").write_text("# API\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "init")
            (repo / "view.js").write_text("export function label(t) {\n  return `${t.done ? 'x' : '-'} ${t.title}`;\n}\n",
                                          encoding="utf-8")
            (repo / "task.go").write_text("package task\n\ntype Task struct {\n\tTitle string `json:\"title\"`\n"
                                          "\tDueDate string `json:\"due_date,omitempty\"`\n}\n", encoding="utf-8")
            (repo / "api.md").write_text("# API\n\n`due_date` を返す。\n", encoding="utf-8")
            terms = codd.terms_from_diff(codd.Side("own", repo, []))
        self.assertFalse([t for t in terms if "${" in t or "json:" in t], terms)
        self.assertIn("due_date", terms)

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

    def test_strings_already_in_the_file_are_not_changed(self) -> None:
        # `{"status": …}` を足しても、"status" が前からそのファイルにあれば、変えた文字列ではない
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=ENV)
            git("init", "-q", "-b", "main")
            (repo / "routes.py").write_text('def a():\n    return {"status": "ok"}\n', encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "init")
            (repo / "routes.py").write_text('def a():\n    return {"status": "ok"}\n\n\ndef b():\n    return {"status": "refunded"}\n',
                                            encoding="utf-8")
            ctx = SimpleNamespace(config={"tests": ["**/*.test.*"]})
            self.assertEqual(codd.literals_from_diff(ctx, "", codd.Side("own", repo, [])), ["refunded"])

    def test_strings_a_new_file_reuses_from_other_code_are_not_changed(self) -> None:
        # 新しいファイルが、前からほかのコードにある文字列を使っただけなら変えた文字列ではない。テストにだけあった文字列は数える
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=ENV)
            git("init", "-q", "-b", "main")
            (repo / "service.py").write_text('def cancel(o):\n    o.status = "cancelled"\n', encoding="utf-8")
            (repo / "service.test.py").write_text('assert x == "partly_refunded"\n', encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "init")
            (repo / "refund.py").write_text('def refund(o):\n    if o.status == "cancelled":\n        return "partly_refunded"\n'
                                            '    o.status = "refunded"\n', encoding="utf-8")
            ctx = SimpleNamespace(config={"tests": ["**/*.test.*"]})
            self.assertEqual(sorted(codd.literals_from_diff(ctx, "", codd.Side("own", repo, []))),
                             ["partly_refunded", "refunded"])

    def test_imports_are_not_screen_text(self) -> None:
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=ENV)
            git("init", "-q", "-b", "main")
            (repo / "a.txt").write_text("x\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "init")
            (repo / "Reset.tsx").write_text('import { useState } from "react";\nimport { X } from "../components/X";\n'
                                            'export const Y = () => <button>再設定する</button>;\n', encoding="utf-8")
            ctx = SimpleNamespace(config={"tests": ["**/*.test.*"]})
            self.assertEqual(codd.literals_from_diff(ctx, "", codd.Side("own", repo, [])), ["再設定する"])


class HtmlInStringsTest(unittest.TestCase):
    def test_text_between_tags_inside_a_string_counts(self) -> None:
        # 文字列で HTML を組む画面（`return '<h1>Hello page</h1>'`）。e2e のケースはタグの間の文言で書く。
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=ENV)
            git("init", "-q", "-b", "main")
            (repo / "page.js").write_text("export const page = () => '<h1>Hello page</h1>';\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "init")
            (repo / "page.js").write_text("export const page = () => '<h1>Welcome page</h1>';\n", encoding="utf-8")
            ctx = SimpleNamespace(config={"tests": ["**/*.test.*"]})
            texts = codd.literals_from_diff(ctx, "", codd.Side("own", repo, []))
        self.assertIn("Hello page", texts)
        self.assertIn("Welcome page", texts)


class ReorderedLiteralsTest(unittest.TestCase):
    def test_items_added_to_a_line_do_not_count_unchanged_strings(self) -> None:
        # 長い f 文字列の中の引用符で組を取り違えると、並べ替えただけの行の `status` を「変えた文字列」として拾い、
        # `res.status` と書いただけのテストまで「響くテスト」にしていた。
        from types import SimpleNamespace
        before = ('def notify(order):\n'
                  '    return f"to={order[\'email\']} order={order[\'order_id\']} status={order[\'status\']} and more"\n'
                  'def make(body):\n    return {"email": body["email"], "status": "PENDING"}\n')
        after = ('def notify(order):\n'
                 '    message = f"to={order[\'email\']} order={order[\'order_id\']} status={order[\'status\']} and more"\n'
                 '    return message\n'
                 'def make(body):\n    return {"email": body["email"], "note": body.get("note", ""), "status": "PENDING"}\n')
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=ENV)
            git("init", "-q", "-b", "main")
            (repo / "orders.py").write_text(before, encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "init")
            (repo / "orders.py").write_text(after, encoding="utf-8")
            ctx = SimpleNamespace(config={"tests": ["**/test_*.py"]})
            self.assertEqual(codd.literals_from_diff(ctx, "", codd.Side("own", repo, [])), ["note"])


class QualifiedNamesTest(unittest.TestCase):
    def test_qualified_names_are_not_new_files(self) -> None:
        # 計画に `status.COMPLETED` と書くと、まだ無いファイル `status.COMPLETE` を変えると読んでいた。
        self.assertIsNone(codd._CITE.fullmatch("status.COMPLETED"))
        self.assertIsNone(codd._NEW_FILE.search("status.PAID"))
        self.assertIsNotNone(codd._NEW_FILE.search("docs/orders.md"))
        self.assertEqual(codd._CITE.match("app.py:12").group("path"), "app.py")


class ScenarioReplayTest(unittest.TestCase):
    """シナリオの再生（tests/scenarios/）で見つけたもの。"""

    def repo(self, tmp: str, files: dict[str, str]):
        repo = Path(tmp)
        git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=ENV)
        git("init", "-q", "-b", "main")
        for rel, body in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(body, encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "init")
        return repo

    def test_a_deleted_file_counts_its_names_as_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp, {"src/old.py": "def old_format():\n    return 0\n", "src/a.py": "x = 1\n"})
            (repo / "src/old.py").unlink()
            self.assertIn("old_format", codd.removed_names(None, "", codd.Side("own", repo, [])))

    def test_a_signature_changed_only_by_a_rename_is_not_a_changed_name(self) -> None:
        # 既定値の定数を改めただけで定義の行が変わった関数は、関数の名前を拾わない（題に名前を書いたテストにまで当たる）
        before = ("export const defaultLocale = 'ja-JP';\n"
                  "export function formatMoney(amount, locale = defaultLocale) {\n  return amount;\n}\n")
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp, {"money.js": before})
            (repo / "money.js").write_text(before.replace("defaultLocale", "fallbackLocale"), encoding="utf-8")
            terms = codd.terms_from_diff(codd.Side("own", repo, []))
        self.assertIn("fallbackLocale", terms)
        self.assertNotIn("formatMoney", terms)

    def test_code_fragments_in_backticks_name_their_head(self) -> None:
        names = codd.name_terms("`discount: int = 0`・`order.set_status(\"cancelled\")`・`note=備考`・`Math.floor`・"
                                "`docs/a.md`・`app.py`")
        self.assertEqual(names, ["discount", "order.set_status", "note", "Math.floor"])

    def test_names_a_deleted_doc_only_mentions_are_not_changed(self) -> None:
        # 消した文書が `…` で触れていただけの名前（移る先の関数）と、その見出しは変わっていない
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp, {"docs/migration.md": "# 移る\n\n## ロケール\n\n`formatMoneyV1` は `formatMoney` に。\n",
                                   "src/money.js": "export function formatMoney() {}\n"})
            (repo / "docs/migration.md").unlink()
            terms = codd.terms_from_diff(codd.Side("own", repo, []))
        self.assertNotIn("formatMoney", terms)
        self.assertNotIn("ロケール", terms)

    def test_a_file_committed_midway_then_reverted_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp, {"a.txt": "a\n", "c.txt": "c\n"})
            side = codd.Side("own", repo, [])
            before = codd.snapshot(side)
            git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=ENV)
            (repo / "a.txt").write_text("A\n", encoding="utf-8")
            (repo / "new.txt").write_text("n\n", encoding="utf-8")
            (repo / "c.txt").unlink()
            git("add", "-A")
            git("commit", "-q", "-m", "midway")
            self.assertEqual(sorted(codd.changed_since(side, before)), ["a.txt", "c.txt", "new.txt"])
            (repo / "a.txt").write_text("a\n", encoding="utf-8")   # コミットしたあと、作業中に戻した
            self.assertEqual(sorted(codd.changed_since(side, before)), ["c.txt", "new.txt"])


class CopiedHeadingsTest(unittest.TestCase):
    def test_headings_copied_from_sibling_docs_are_not_changed_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=ENV)
            git("init", "-q", "-b", "main")
            (repo / "login.md").write_text("# ログイン画面\n\n## 画面\n\n## 流れ\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "init")
            (repo / "reset.md").write_text("# 再設定画面\n\n## 画面\n\n## 流れ\n", encoding="utf-8")
            terms = codd.terms_from_diff(codd.Side("own", repo, []))
        self.assertEqual(terms, ["再設定画面"])   # 書式に合わせた見出し（画面・流れ）は、ありふれた語に当たるだけ


class IncomingMergeTest(unittest.TestCase):
    def test_names_brought_in_by_a_merge_are_not_measured(self) -> None:
        # 競合の解消で取り込んだ変更（ほかの人が済ませたもの）の名前は、この回に変えた名前ではない
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=ENV)
            git("init", "-q", "-b", "main")
            (repo / "app.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "init")
            git("checkout", "-q", "-b", "other")
            (repo / "far.py").write_text("def far_away():\n    return 2\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "other")
            git("checkout", "-q", "main")
            git("merge", "-q", "--no-commit", "--no-ff", "other")
            (repo / "app.py").write_text("def hello_world():\n    return 1\n", encoding="utf-8")
            terms = codd.terms_from_diff(codd.Side("own", repo, []))
        self.assertIn("hello_world", terms)
        self.assertNotIn("far_away", terms)

    def test_only_files_changed_in_this_run_give_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=ENV)
            git("init", "-q", "-b", "main")
            (repo / "a.py").write_text("X = 1\n", encoding="utf-8")
            (repo / "b.py").write_text("Y = 1\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "init")
            (repo / "a.py").write_text("def mine():\n    return 1\n", encoding="utf-8")
            (repo / "b.py").write_text("def before_the_run():\n    return 1\n", encoding="utf-8")
            terms = codd.terms_from_diff(codd.Side("own", repo, []), {"a.py"})
        self.assertIn("mine", terms)
        self.assertNotIn("before_the_run", terms)   # 変える前から作業中だった変更


class PlanNamesTest(unittest.TestCase):
    def test_ellipsis_around_a_name_is_dropped_and_inside_is_flagged(self) -> None:
        # `…sourceId…` と書いても sourceId で測る。途中の省略は、どこにも当たらないので書き直させる
        self.assertEqual(codd.name_terms("- a.py — `…sourceId…` と `...name` を直す"), ["sourceId", "name"])
        self.assertEqual(codd.elided_names("`get…Id` と `…sourceId…` と `a b…`"), ["get…Id"])

    def test_keywords_declarations_headings_and_routes(self) -> None:
        # `if (sourceId)` は sourceId、`const disabled = isGeneratedPrintDisabled;` は右辺、`## usePreviewModel` は見出しの名前で探す
        self.assertEqual(codd.name_terms("`if (sourceId)`"), ["sourceId"])
        self.assertEqual(codd.name_terms("`const disabled = isGeneratedPrintDisabled;`"), ["isGeneratedPrintDisabled"])
        self.assertEqual(codd.name_terms("`const MAX = 3`"), ["MAX"])
        self.assertEqual(codd.name_terms("`## usePreviewModel`"), ["usePreviewModel"])
        self.assertEqual(codd.name_terms("`POST /orders/{id}/refund` と `src/a.py`"), ["/orders/{id}/refund"])


class GrepTermsTest(unittest.TestCase):
    def test_terms_are_searched_together_and_split_back_per_term(self) -> None:
        # git を語ごとに動かさず、語単位とそうでないものの 2 回で引き、行を語に振り分ける
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=ENV)
            git("init", "-q", "-b", "main")
            (repo / "a.py").write_text("hello()\nhelloWorld()\nprint('サインイン')\n", encoding="utf-8")
            (repo / "b.md").write_text("HELLO は挨拶。\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "init")
            found = codd.grep_terms(codd.Side("own", repo, []), ["hello", "サインイン", "missing"])
        self.assertEqual(found["hello"], ["a.py:1:hello()", "b.md:1:HELLO は挨拶。"])   # 語単位・大文字小文字を問わない
        self.assertEqual(found["サインイン"], ["a.py:3:print('サインイン')"])
        self.assertEqual(found["missing"], [])


if __name__ == "__main__":
    unittest.main()
