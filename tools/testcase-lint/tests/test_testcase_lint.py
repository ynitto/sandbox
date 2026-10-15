"""testcase-lint の単体テスト。python -m unittest discover -s tools/testcase-lint/tests"""

import io
import json
import os
import random
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from testcase_lint import text as tx  # noqa: E402
from testcase_lint.checks import run_checks  # noqa: E402
from testcase_lint.cli import main  # noqa: E402
from testcase_lint.config import Config, load_config  # noqa: E402
from testcase_lint.loader import FieldMap, load_file, load_requirements, strip_fences  # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples")


_TMP = tempfile.TemporaryDirectory()


def tearDownModule():
    _TMP.cleanup()


class Tmp:
    def __init__(self):
        self.dir = tempfile.mkdtemp(dir=_TMP.name)

    def write(self, name, content):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(content).lstrip("\n"))
        return path


def lint(content, name="cases.yaml", requirements=None, config=None):
    t = Tmp()
    path = t.write(name, content)
    cases, findings = load_file(path, name, FieldMap((config or Config()).fields))
    reqs, lines = (None, {})
    if requirements is not None:
        reqs, lines = load_requirements(t.write("reqs.yaml", requirements), FieldMap())
    findings += run_checks(cases, config or Config(), reqs, "reqs.yaml", lines)
    return cases, findings


def codes(findings):
    return sorted({f.code for f in findings})


def by_code(findings, code):
    return [f for f in findings if f.code == code]


def run_cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(list(args))
    return rc, out.getvalue(), err.getvalue()


class LoaderTest(unittest.TestCase):
    def test_japanese_keys_and_line_numbers(self):
        cases, _ = lint("""
            テストケース:
              - テストID: T-1
                テスト項目: ログイン
                前提条件: 登録済み
                手順: |
                  1. 画面を開く
                  2. ログインする
                期待結果: ホーム画面が表示される
        """)
        self.assertEqual(len(cases), 1)
        c = cases[0]
        self.assertEqual((c.id, c.title), ("T-1", "ログイン"))
        self.assertEqual([i.text for i in c.steps], ["画面を開く", "ログインする"])
        self.assertEqual([i.line for i in c.steps], [6, 7])
        self.assertEqual(c.expected[0].text, "ホーム画面が表示される")
        self.assertEqual(c.line, 2)

    def test_gherkin_keys(self):
        cases, _ = lint("""
            scenarios:
              - name: 検索
                given: 商品が 3 件ある
                when: [検索欄に「りんご」と入力する, 検索ボタンを押す]
                then: りんごの商品が 1 件表示される
        """)
        c = cases[0]
        self.assertEqual(len(c.preconditions), 1)
        self.assertEqual(len(c.steps), 2)
        self.assertEqual(len(c.expected), 1)

    def test_structured_steps_with_expect_go_to_expected(self):
        cases, _ = lint("""
            suite: s
            variants:
              - { name: ja, locale: ja-JP }
            cases:
              - id: TC-1
                title: t
                mocks: [{ url: "**/api", status: 500 }]
                steps:
                  - goto: /
                  - click: { role: button, name: 保存 }
                  - expect: { target: { role: status }, text: 保存しました }
        """)
        self.assertEqual(len(cases), 1, "variants はケースとして読まない")
        c = cases[0]
        self.assertEqual(len(c.steps), 2)
        self.assertIn("保存しました", c.expected[0].text)
        self.assertTrue(c.expected[0].field.startswith("steps[2]."))
        self.assertTrue(any("mocks" in i.text for i in c.preconditions), "知らない項目は前提に入る")

    def test_step_dicts_with_action_and_expected(self):
        cases, _ = lint("""
            - id: A
              title: t
              steps:
                - { action: 保存ボタンを押す, expected: 保存しました と表示される }
        """)
        c = cases[0]
        self.assertEqual(c.steps[0].text, "保存ボタンを押す")
        self.assertEqual(c.expected[0].text, "保存しました と表示される")

    def test_checklist_of_strings(self):
        cases, _ = lint('{"checklist": ["文言が仕様書と一致すること", "ボタンが 2 つあること"]}', name="c.json")
        self.assertEqual([c.kind for c in cases], ["check", "check"])
        self.assertEqual(cases[1].line, 1)

    def test_fenced_block_keeps_line_numbers(self):
        text = "説明です\n\n```yaml\n- id: A\n  title: t\n  steps: [x]\n  expected: [y]\n```\n"
        self.assertEqual(strip_fences(text).split("\n")[3], "- id: A")
        cases, _ = lint(text, name="out.md")
        self.assertEqual(cases[0].line, 4)

    def test_duplicate_key_and_parse_error(self):
        _, f = lint('[{"id": "A", "title": "t", "id": "B"}]', name="c.json")
        self.assertEqual(len(by_code(f, "D15")), 1)
        _, f = lint("cases:\n  - id: [unclosed\n")
        self.assertEqual(codes(f), ["D00"])
        self.assertGreater(f[0].line, 1)

    def test_requirements_forms(self):
        t = Tmp()
        for content in ("[R1, R2]", "R1: 本文\nR2: 本文2\n", "requirements:\n  - {id: R1, text: a}\n  - {id: R2}\n"):
            reqs, lines = load_requirements(t.write("r.yaml", content), FieldMap())
            self.assertEqual(sorted(reqs), ["R1", "R2"], content)
            self.assertTrue(all(lines[k] for k in reqs), content)


class DefectRulesTest(unittest.TestCase):
    def test_missing_parts(self):
        _, f = lint("""
            - id: A
              steps: [押す]
            - id: B
              title: t
              expected: [出る]
        """)
        self.assertEqual(codes(f), ["D03", "D04", "D05", "G02"])

    def test_ids(self):
        _, f = lint("""
            - {id: A, title: t1, steps: [a], expected: [エラーが出る]}
            - {id: A, title: t2, steps: [b], expected: [c]}
            - {title: t3, steps: [d], expected: [e]}
        """)
        self.assertEqual(len(by_code(f, "D02")), 1)
        self.assertEqual(len(by_code(f, "D01")), 1)

    def test_same_id_in_different_files_is_fine(self):
        t = Tmp()
        a = t.write("a.yaml", "- {id: TC-1, title: x, steps: [a], expected: [エラー]}\n")
        b = t.write("b.yaml", "- {id: TC-1, title: y, steps: [b], expected: [エラー]}\n")
        rc, out, _ = run_cli(a, b, "-f", "json")
        self.assertNotIn("D02", [x["code"] for x in json.loads(out)["findings"]])

    def test_wording_rules(self):
        _, f = lint("""
            - id: A
              title: t
              steps:
                - 任意のユーザー名を入力する
                - 前のケースと同じ手順で保存する
                - 一覧が表示されること
                - 画面を開いて、名前を入力して、日付を選んで、保存を押して、閉じる
              expected:
                - エラーメッセージが正しく表示される
                - 保存ボタンをクリックする
                - TBD
        """)
        self.assertEqual([x for x in codes(f) if x.startswith("D")], ["D06", "D07", "D08", "D09", "D10", "D11", "D12"])
        self.assertEqual(by_code(f, "D06")[0].line, 9)

    def test_clean_case_has_no_defects(self):
        _, f = lint("""
            - id: A
              title: 8 文字のパスワードで登録できる
              steps: [パスワード欄に「abcdefgh」を入力する, 登録ボタンを押す]
              expected: [「登録しました」と表示される]
            - id: B
              title: 7 文字のパスワードは登録できない
              steps: [パスワード欄に「abcdefg」を入力する, 登録ボタンを押す]
              expected: [「8 文字以上で入力してください」と表示される]
        """)
        self.assertEqual([x for x in codes(f) if x.startswith("D")], [])

    def test_equal_sign_word_is_not_vague(self):
        _, f = lint("- {id: A, title: t, steps: [計算する], expected: [合計が小計と等しい]}\n")
        self.assertEqual(by_code(f, "D06"), [])

    def test_requirement_links(self):
        _, f = lint("""
            - {id: A, title: a, requirement: R1, steps: [x], expected: [エラー]}
            - {id: B, title: b, requirement: R9, steps: [y], expected: [z]}
            - {id: C, title: c, steps: [w], expected: [v]}
        """, requirements="[R1, R2]")
        self.assertEqual(len(by_code(f, "D13")), 1)
        self.assertEqual(by_code(f, "D14")[0].cases, ["B"])
        g01 = by_code(f, "G01")
        self.assertEqual(len(g01), 1)
        self.assertIn("R2", g01[0].message)
        self.assertEqual((g01[0].file, g01[0].line), ("reqs.yaml", 1))


class DuplicateAndConflictTest(unittest.TestCase):
    BASE = """
        - id: A
          title: ログインできる
          steps: [ログイン画面を開く, メールに「u@example.com」を入力する, ログインボタンを押す]
          expected: [{exp_a}]
        - id: B
          title: {title_b}
          steps: [ログイン画面を開く, メールに「u@example.com」を入力する, ログインボタンを押す]
          expected: [{exp_b}]
    """

    def case(self, exp_a, exp_b, title_b="ログインできる2"):
        return lint(self.BASE.format(exp_a=exp_a, exp_b=exp_b, title_b=title_b))[1]

    def test_exact_duplicate(self):
        f = self.case("ホーム画面が表示される", "ホーム画面が表示される")
        self.assertEqual(len(by_code(f, "U01")), 1)
        self.assertEqual(by_code(f, "U01")[0].cases, ["A", "B"])
        self.assertEqual(by_code(f, "U02"), [])

    def test_near_duplicate(self):
        f = self.case("ホーム画面が表示される", "ホームの画面が表示される")
        self.assertEqual(len(by_code(f, "U02")), 1)

    def test_opposite_expected_is_conflict_not_duplicate(self):
        f = self.case("ホーム画面が表示される", "ホーム画面が表示されない")
        self.assertEqual(len(by_code(f, "C01")), 1)
        self.assertEqual(by_code(f, "U02"), [])

    def test_different_expected(self):
        f = self.case("ホーム画面が表示される", "アカウントがロックされたとメールが届く")
        self.assertEqual(codes([x for x in f if x.code.startswith("C")]), ["C03"])

    def test_different_values_are_not_compared(self):
        _, f = lint("""
            - {id: A, title: a, steps: [パスワードに「secret」を入力する, ログインを押す], expected: [ホーム画面が表示される]}
            - {id: B, title: b, steps: [パスワードに「wrong」を入力する, ログインを押す], expected: [ホーム画面が表示されない]}
        """)
        self.assertEqual([x for x in codes(f) if x[0] in "CU"], [])

    def test_value_variants(self):
        rows = "\n".join(f"- {{id: V{i}, title: v{i}, steps: [数量に「{i}」を入力する, 計算を押す], "
                         f"expected: [合計が「{i * 100}」円と表示される]}}" for i in range(1, 5))
        _, f = lint(rows)
        u03 = by_code(f, "U03")
        self.assertEqual(len(u03), 1)
        self.assertEqual(len(u03[0].cases), 4)

    def test_same_title(self):
        f = self.case("ホーム画面が表示される", "メニューが開く", title_b="ログインできる")
        self.assertEqual(len(by_code(f, "U04")), 1)

    def test_conflict_inside_case(self):
        _, f = lint("""
            - id: A
              title: t
              preconditions: [ログイン済みである, 未ログインである]
              steps: [保存を押す]
              expected: [保存ボタンが有効になる, 保存ボタンが無効になる]
        """)
        self.assertEqual(len(by_code(f, "C02")), 2)

    def test_sequential_expectations_are_not_conflict(self):
        _, f = lint("""
            cases:
              - id: A
                title: 再試行
                steps:
                  - expect: { visible: { text: 読み込めませんでした } }
                  - click: { role: button, name: 再試行 }
                  - expect: { hidden: { text: 読み込めませんでした } }
        """)
        self.assertEqual(by_code(f, "C02"), [])

    def test_numeric_conflict_and_boundaries(self):
        _, f = lint("""
            - id: A
              title: 上限
              preconditions: [パスワードは最大30文字]
              steps: [パスワードに30文字を入力する, 保存を押す]
              expected: [保存できる]
            - id: B
              title: 下限
              steps: [パスワードに7文字を入力する, 保存を押す]
              expected: [エラーが表示される]
        """, requirements="R1: パスワードは8文字以上20文字以下\n")
        c04 = by_code(f, "C04")
        self.assertEqual(len(c04), 1)
        self.assertIn("20文字以下", c04[0].message)
        g03 = {f.message for f in by_code(f, "G03")}
        self.assertTrue(any("8文字以上" in m and "8文字" in m.split("境界")[1] for m in g03), g03)
        self.assertTrue(any("20文字以下" in m and "20文字・21文字" in m for m in g03), g03)
        self.assertTrue(any("最大30文字" in m and "31文字" in m for m in g03), g03)

    def test_quoted_input_length_counts_as_boundary(self):
        _, f = lint("""
            - {id: A, title: 下限ちょうど, steps: [パスワードに「abcdefgh」を入力する], expected: [登録できる]}
            - {id: B, title: 下限の外, steps: [パスワードに「abcdefg」を入力する], expected: [エラーが表示される]}
        """, requirements="R1: パスワードは8文字以上\n")
        self.assertEqual(by_code(f, "G03"), [])


class GapRulesTest(unittest.TestCase):
    def test_no_negative_case(self):
        _, f = lint("""
            feature: 検索
            cases:
              - {id: A, title: a, steps: [検索する], expected: [結果が出る]}
              - {id: B, title: b, steps: [並べ替える], expected: [順番が変わる]}
        """)
        g02 = by_code(f, "G02")
        self.assertEqual(len(g02), 1)
        self.assertIn("検索", g02[0].message)

    def test_negative_case_present(self):
        _, f = lint("""
            - {id: A, title: a, steps: [検索する], expected: [結果が出る]}
            - {id: B, title: b, steps: [未入力で検索する], expected: [メッセージが出る]}
        """)
        self.assertEqual(by_code(f, "G02"), [])

    def test_pairs(self):
        _, f = lint("""
            - {id: A, title: a, preconditions: [ログイン済み], steps: [開く], expected: [エラーにならない]}
        """)
        self.assertTrue(any("ログインしていない" in x.message for x in by_code(f, "G04")))
        _, f = lint("""
            - {id: A, title: a, preconditions: [ログイン済み], steps: [開く], expected: [エラーにならない]}
            - {id: B, title: b, preconditions: [未ログイン], steps: [開く], expected: [ログイン画面へ移る]}
        """)
        self.assertFalse(any("ログイン" in x.message for x in by_code(f, "G04")))

    def test_custom_pair_from_config(self):
        cfg = Config(pairs=[{"a_label": "会員", "b_label": "非会員", "a": ["(?<!非)会員"], "b": ["非会員"]}])
        _, f = lint("- {id: A, title: 会員が買う, steps: [買う], expected: [エラーにならない]}\n", config=cfg)
        self.assertTrue(any("非会員" in x.message for x in by_code(f, "G04")))


class TextTest(unittest.TestCase):
    def test_polarity(self):
        self.assertIsNotNone(tx.opposite("ログインできる", "ログインできない"))
        self.assertIsNotNone(tx.opposite("ログインに成功する", "ログインに失敗する"))
        self.assertIsNone(tx.opposite("一覧が表示される", "詳細が表示される"))
        # 未 と ない は打ち消し合う
        self.assertIsNone(tx.opposite("未入力ならエラーが表示される", "入力したらエラーが表示されない"))

    def test_constraints(self):
        ks = tx.constraints("ユーザー名は最大30文字。注文は10件未満。参加者は3名以上")
        got = {(k.subject, k.unit, k.kind, k.value) for k in ks}
        self.assertEqual(got, {("ユーザー名", "文字", "max", 30), ("注文", "件", "max", 9), ("参加者", "名", "min", 3)})

    def test_similar_pairs_matches_brute_force(self):
        rnd = random.Random(7)
        words = ["画面", "開く", "保存", "一覧", "検索", "削除", "設定", "表示", "入力", "押す"]
        texts = ["".join(rnd.choice(words) for _ in range(rnd.randint(3, 8))) for _ in range(150)]
        texts += [t + "の" for t in texts[:20]]
        vecs = tx.vectorize(texts)
        for th in (0.7, 0.9):
            fast = {(i, j) for i, j, _ in tx.similar_pairs(vecs, th)}
            brute = {(i, j) for i in range(len(vecs)) for j in range(i + 1, len(vecs))
                     if tx.cosine(vecs[i], vecs[j]) >= th}
            self.assertEqual(fast, brute)


class CliTest(unittest.TestCase):
    def test_examples(self):
        rc, out, _ = run_cli(os.path.join(EXAMPLES, "login-cases.yaml"), os.path.join(EXAMPLES, "checklist.json"),
                             "-r", os.path.join(EXAMPLES, "requirements.yaml"), "-f", "json")
        self.assertEqual(rc, 1)
        data = json.loads(out)
        got = {f["code"] for f in data["findings"]}
        for code in ("C01", "C04", "D02", "D06", "D07", "D09", "D10", "D14", "G01", "G03", "U01"):
            self.assertIn(code, got)
        self.assertEqual(data["summary"]["cases"], 13)

    def test_fail_on_and_min_severity(self):
        t = Tmp()
        p = t.write("c.yaml", "- {id: A, title: a, steps: [x], expected: [エラーが正しく出る]}\n")
        self.assertEqual(run_cli(p)[0], 0)
        self.assertEqual(run_cli(p, "--fail-on", "warning")[0], 1)
        rc, out, _ = run_cli(p, "--min-severity", "error")
        self.assertNotIn("D06", out)

    def test_config_turns_rules_off(self):
        t = Tmp()
        p = t.write("c.yaml", "- {id: A, title: a, steps: [x], expected: [正しく出る]}\n")
        cfg = t.write("conf.yaml", "rules: {D06: off, G02: error}\n")
        rc, out, _ = run_cli(p, "-c", cfg, "-f", "json")
        got = {f["code"]: f["severity"] for f in json.loads(out)["findings"]}
        self.assertNotIn("D06", got)
        self.assertEqual(got.get("G02"), "error")
        self.assertEqual(rc, 1)
        bad = t.write("bad.yaml", "rules: {Z99: off}\n")
        self.assertEqual(run_cli(p, "-c", bad)[0], 2)
        with self.assertRaises(ValueError):
            load_config(t.write("bad2.yaml", "rules: {D06: loud}\n"))

    def test_markdown_and_folder(self):
        rc, out, _ = run_cli(EXAMPLES, "-f", "markdown", "--fail-on", "never")
        self.assertEqual(rc, 0)
        self.assertIn("## 矛盾", out)

    def test_usage_errors(self):
        self.assertEqual(run_cli()[0], 2)
        self.assertEqual(run_cli(os.path.join(EXAMPLES, "nothing-*.yaml"))[0], 2)
        rc, out, _ = run_cli("--list-rules")
        self.assertEqual(rc, 0)
        self.assertIn("G03", out)


if __name__ == "__main__":
    unittest.main()
