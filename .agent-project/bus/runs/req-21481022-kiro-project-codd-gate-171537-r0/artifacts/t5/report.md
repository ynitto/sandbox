# t5: kiro_project/coddgate.py 新規作成（モジュール骨格・定数・_codd_gate_bin）

**切り口**: 他候補が「t1〜t3 が既報告した `kiro_project/` パッケージ不在をブロッカーとして
再報告するだけ」「または main の refactor を丸ごと取り込む」に傾く可能性がある中、本候補は
**最小差分で前進させる**（ディレクトリと `coddgate.py` 1ファイルのみ新規作成し、
`__init__.py`／`_FRAGMENTS` 登録・main merge には一切手を出さない）を選んだ。

## 成果

`tools/kiro-project/kiro_project/coddgate.py` を新規作成（ディレクトリごと新規）。

```python
from __future__ import annotations
# coddgate.py — codd-gate 自動検出・結線用の新規断片（既存ファイルの機械分割ではない）。
# 単体 import しない。kiro_project/__init__.py が _FRAGMENTS 経由で共有名前空間へ exec 合成する
#（本ファイル単体は現時点で未結線。__init__.py 側の _FRAGMENTS 登録・shutil 等の import は
#  _head.py が担う前提で、このファイルではモジュールレベル import を行わない）。
# codd-gate バイナリの検出・存在判定。
# ---------------------------------------------------------------------------

CODD_GATE_BINARY_NAME = "codd-gate"


def _codd_gate_bin() -> "str | None":
    return shutil.which(CODD_GATE_BINARY_NAME)
```

- 他に変更したファイルなし（`git status --short` は `?? tools/kiro-project/kiro_project/` の
  1行のみ）。

## 採用した前提

1. **配置場所**: タスク文の `kiro_project/coddgate.py` は `tools/kiro-project/kiro_project/coddgate.py`
   （run 全体の完了条件のコマンドが参照するパス）と解釈した。この階層は作業ブランチに
   存在しなかったため、本タスクの範囲内でディレクトリごと新規作成した。
2. **断片規約**: t1 が `main:tools/kiro-project/kiro_project/verify.py` から採取した規約
   （1行目 `from __future__ import annotations`／2〜4行目に「単体 import しない」自己言及＋
   機能要約コメント／モジュールレベル import なし／型注釈は文字列リテラル併用）をそのまま
   踏襲した。`coddgate.py` は機械分割ではなく新規断片のため「元ファイル行範囲」コメントは
   省略（t1 の指摘どおり該当しない）。
3. **`shutil` 未 import の妥当性**: `main:_head.py` を確認し、`shutil` は既に import 済みで
   あることを確認した（`_head.py` 内 `import shutil` 行を実見）。よって `_head` 相当の断片が
   合成された時点で `_codd_gate_bin()` は問題なく動く設計になっている。ただし**このブランチには
   `_head.py` 自体がまだ無い**ため、現時点でこのファイルを単独 import/exec すると
   `NameError: shutil` になる——これは規約どおりの「単体 import しない」制約の帰結であり、
   バグではない。
4. **定数名 `CODD_GATE_BINARY_NAME`**: t3 が定義した最終API命名（既存 `codd_gate_detect.py` の
   `BINARY_NAME` → 統合後 `CODD_GATE_BINARY_NAME` にリネーム推奨）にあらかじめ合わせた。
   将来 t6 以降が `codd_gate_detect.py` 由来の関数群をこの同じファイルへ移送する際、
   定数の二重定義を避けられる。
5. **関数シグネチャ**: タスク文「shutil.which("codd-gate") によるバイナリ存在判定」を
   文字どおり実装。戻り値は `str | None`（見つかった絶対パス、または見つからなければ
   `None`）とし、真偽値としても素直に使える設計にした（`resolve_codd_gate_bin` 等
   t3 棚卸し済みの既存パターンと戻り値の型を揃えた）。

## 範囲外として意図的にやらなかったこと

- **`__init__.py` の新規作成・`_FRAGMENTS` タプルへの `"coddgate"` 登録**: t1 が確定した
  挿入位置（`"prioritize"` の直後・`"verify"` の直前）は把握済みだが、`__init__.py` 自体の
  作成は本タスクの指示文（「モジュール骨格・定数・`_codd_gate_bin()`」の実装）に含まれない
  ため着手していない。現状この `coddgate.py` は `kiro_project` パッケージとして未結線
  （`__init__.py` が無いため `import kiro_project` 自体が意味を持たない）。
- **main のパッケージ化 refactor の merge/rebase**: t1 が「評価役の意思決定が必要」と
  明示した論点であり、本タスク単独で判断・実行しない。
- **`_codd_gate_bin()` 以外の関数**（`codd_gate_enabled` 等、t2/t3 が予告した t6-t9 分の
  シンボル移送）は範囲外のため未実装。
- **新規テストの追加**: 現状 `coddgate.py` は断片規約上「単体 import 不可」であり、
  `__init__.py` の合成機構が無い状態でテストを書くには規約に反する `import shutil` を
  このファイルへ追加するか、合成ハーネスを自作するかのどちらかが必要になる。いずれも
  本タスクの指示（骨格＋定数＋関数の実装のみ）を超えるため見送った。

## 検証

- `python3 -m py_compile tools/kiro-project/kiro_project/coddgate.py` → 構文エラーなし（OK）。
- `grep -rq "codd_gate" tools/kiro-project/kiro_project/` → 終了コード 0（マッチあり）。
- `python3 -m pytest tools/kiro-project/tests -q -k codd` → **63 passed**（t2/t3 のベースラインと
  同値、新規ファイルはどこからも import されないため無回帰であることを確認）。
- `git status --short` → `?? tools/kiro-project/kiro_project/` のみ。既存ファイルへの変更なし。
- `codd-gate verify --repos ... --strict`（run 全体の完了条件）は**本タスクの範囲外**として
  未実行——`__init__.py`／フックへの結線が別タスク（t6 以降・gate タスク）の担当であるため、
  現状のワークツリーで実行しても意味のある成否判定にならない。参考情報として、
  `codd-gate` バイナリ自体は本マシンの PATH 上（`/Users/nitto/.local/bin/codd-gate`）に
  存在することは確認した。

## 未解決事項（引き継ぎ）

- t1〜t3 既報告の「作業ブランチに `kiro_project/` パッケージが存在しない」問題は、
  本タスクでディレクトリと `coddgate.py` を作ったことで**部分的に前進**したが、
  `__init__.py`／`_FRAGMENTS` 登録・main merge の意思決定は依然未解決。次工程
  （t6 以降、あるいは統合専任タスク）が着手する前に、評価役が
  「main の refactor を merge する」か「`__init__.py` を本タスク系列で新規に組む」かを
  決める必要がある。
- t3 が報告した「`codd_gate_hooks.py`/`codd_gate_invoke.py` の棚卸し未割当」「デッドコード3件の
  扱い未定」は本タスクでも未解消のまま引き継ぐ。
