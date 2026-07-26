"""モジュール直下の `def test_*` を `unittest discover` に拾わせるための橋渡し。

**なぜ要るか**: `unittest discover` が集めるのは `unittest.TestCase` のサブクラスだけで、
モジュール直下の関数は**黙って無視される**。`resident` パッケージの単体テスト 4 ファイル
（scheduler / supervisor / worker / status）は関数形式で書かれており、`if __name__` の
手動呼び出しでしか走っていなかった——CI（P3-1）は `unittest discover` を回すので、
**31 件が緑とも赤とも報告されないまま**通り過ぎていた（P2 の実装時に実測で発見）。

pytest を足す選択は採らない（このリポジトリのテストは stdlib だけで走るのが規約で、
CI もそれを前提に依存を入れていない）。標準の `load_tests` プロトコルと
`unittest.FunctionTestCase` で足りる。

使い方（各ファイルの末尾に 1 行）:

    from _functest import module_load_tests
    load_tests = module_load_tests(globals())

`if __name__ == "__main__"` の直接実行も従来どおり動く（こちらは何も変えない）。
"""
from __future__ import annotations

import unittest


def module_load_tests(namespace: dict):
    """`load_tests` フックを作る。`namespace` は呼び出し側の `globals()`。

    拾うのは「そのモジュールで定義された」`test_*` 関数だけ——`from x import *` で
    入ってきた他モジュールのテストを二重に走らせない（`__module__` で絞る）。
    名前順に固定するのは、失敗の再現に実行順の当て推量を要らなくするため。
    """
    module_name = namespace.get("__name__")

    def load_tests(loader, standard_tests, pattern):  # noqa: ARG001 — unittest の規定シグネチャ
        suite = unittest.TestSuite()
        suite.addTests(standard_tests)      # 同じファイルに TestCase があれば残す
        for name in sorted(namespace):
            if not name.startswith("test_"):
                continue
            fn = namespace[name]
            if not callable(fn) or getattr(fn, "__module__", None) != module_name:
                continue
            # `description` は失敗レポートの見出しになる。モジュール名まで入れておくと、
            # 4 ファイルに同名のテストがあっても報告からどれか分かる。
            suite.addTest(unittest.FunctionTestCase(
                fn, description=f"{module_name}.{name}"))
        return suite

    return load_tests
