"""単体配布アダプタが持つ環境解決の複製が、正典とずれていないことを縛る。

`~/.profile` からの `OLLAMA_*` 補完とプロキシ迂回は `ollama_adapter.py` が正典だが、
**単体ファイルで配る 2 つ**（`agent-aider` = `aider_adapter.py`、`agent-opencode` =
`tools/opencode/agent-opencode.py`）は agentcore を import できないので同じコードを持つ。

複製そのものは禁じない——1 ファイルで配るという配布形態から来る必然だからだ。危ないのは
**片方だけ直しても両方のテストが緑のまま**であることで、症状は「この CLI だけ接続できない」
「この CLI だけプロキシへ流れて 504」という、環境のせいに見える形で出る。だから一致を
機械に突き合わせさせる（agent-dashboard の JS 写しをゴールデンテストで縛るのと同じ流儀）。

比較は AST で行い、docstring とコメントは無視する。**説明は各ファイルの文脈で違ってよく、
揃っていなければならないのは振る舞いだけ**だからだ。
"""
from __future__ import annotations

import ast
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[5]

CANON = _REPO / "tools" / "agent-tools" / "agentcore" / "agentcore" / "ollama_adapter.py"
COPIES = {
    "agent-aider": _REPO / "tools" / "agent-tools" / "agentcore" / "agentcore" / "aider_adapter.py",
    "agent-opencode": _REPO / "tools" / "opencode" / "agent-opencode.py",
}

# 揃っていなければならない関数と定数。
SHARED_FUNCTIONS = ("_complete_ollama_env", "_import_profile_env", "load_profile_env")
SHARED_CONSTANTS = ("_PROFILE_ENV_PREFIXES", "_PROFILE_ENV_EXACT")


def _strip_docstring(body: "list") -> "list":
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        return body[1:]
    return body


def _functions(path: Path) -> "dict[str, str]":
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: "dict[str, str]" = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in SHARED_FUNCTIONS:
            module = ast.Module(body=_strip_docstring(list(node.body)), type_ignores=[])
            out[node.name] = ast.unparse(module)
    return out


def _constants(path: Path) -> "dict[str, str]":
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: "dict[str, str]" = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in SHARED_CONSTANTS:
                out[target.id] = ast.unparse(node.value)
    return out


class AdapterEnvParityTests(unittest.TestCase):
    def test_every_copy_exists(self):
        """複製が消えた・移動したことに気づかないまま緑にしない。"""
        self.assertTrue(CANON.is_file(), f"正典が見つかりません: {CANON}")
        for name, path in COPIES.items():
            self.assertTrue(path.is_file(), f"{name} の複製が見つかりません: {path}")

    def test_canon_declares_every_shared_name(self):
        """正典に無い名前を比べても意味が無いので、まず正典側を確かめる。"""
        fns, consts = _functions(CANON), _constants(CANON)
        for name in SHARED_FUNCTIONS:
            self.assertIn(name, fns, f"正典に {name} がありません（名前を変えたら本テストも直す）")
        for name in SHARED_CONSTANTS:
            self.assertIn(name, consts, f"正典に {name} がありません")

    def test_shared_functions_match_the_canon(self):
        canon = _functions(CANON)
        for label, path in COPIES.items():
            copy = _functions(path)
            for name in SHARED_FUNCTIONS:
                self.assertIn(name, copy, f"{label} に {name} がありません")
                self.assertEqual(
                    canon[name], copy[name],
                    f"{label} の {name} が正典（ollama_adapter.py）とずれています。"
                    "単体配布ファイルは agentcore を import できないので複製で構いませんが、"
                    "片方だけ直すと『この CLI だけ接続できない』として出ます。両方揃えてください。")

    def test_shared_constants_match_the_canon(self):
        canon = _constants(CANON)
        for label, path in COPIES.items():
            copy = _constants(path)
            for name in SHARED_CONSTANTS:
                self.assertIn(name, copy, f"{label} に {name} がありません")
                self.assertEqual(canon[name], copy[name],
                                 f"{label} の {name} が正典とずれています")

    def test_comments_may_differ(self):
        """説明は各ファイルの文脈で違ってよい——比較対象が振る舞いだけであることの確認。

        docstring を落とさずに比べると常に落ちる（正典の方が説明が厚い）ので、
        この比較器が本当に本文を無視していることを固定しておく。
        """
        canon_src = CANON.read_text(encoding="utf-8")
        aider_src = COPIES["agent-aider"].read_text(encoding="utf-8")
        self.assertNotEqual(canon_src, aider_src, "前提が変わった（同一ファイルになっている）")
        self.assertEqual(_functions(CANON)["load_profile_env"],
                         _functions(COPIES["agent-aider"])["load_profile_env"])


if __name__ == "__main__":
    unittest.main()
