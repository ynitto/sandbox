# t1: _FRAGMENTS 構造の把握と coddgate 挿入位置の確定

## 重大な前提崩れ（最優先で報告）

**`tools/kiro-project/kiro_project/__init__.py` は、このタスクの作業ブランチ
（`kp/kiro-project-codd-gate-171537`, HEAD `99d71b2e`）のワークツリーには存在しない。**

- ブランチ現状: `tools/kiro-project/` は単一ファイル `kiro-project.py`（約64万バイト）＋
  独立した `codd_gate_base.py` / `codd_gate_debt.py` / `codd_gate_detect.py` /
  `codd_gate_hooks.py` / `codd_gate_invoke.py` / `codd_gate_routing.py` /
  `codd_gate_status.py` の7ファイル構成。`kiro_project/` パッケージ自体が無い。
- 一方 **`main` ブランチ**（コミット `1cee8484` "Add verify.py for enhanced
  verification processes"、`main` HEAD `153e757b` でも内容同一）には、まさにこのタスクが
  前提とする `kiro_project/__init__.py` ＋ `_FRAGMENTS` 断片分割パッケージが存在する
  （`_head.py, model.py, policy.py, decisions.py, instances.py, state.py, rules.py,
  needs.py, prioritize.py, verify.py, request.py, flow.py, config.py, batch.py, mr.py,
  stategit.py, loop.py, commands.py, doctor.py, charter.py, plan.py, gitcache.py,
  project.py, configfile.py, update.py, cli.py` の26断片）。
- 両ブランチの分岐点（merge-base）は `0dede1b7`。作業ブランチはこの分岐後、
  パッケージ化リファレンス refactor を取り込まないまま独自に codd_gate_*.py を追加する
  方向で進んでいた（過去ラウンド r8/r9 のコミットが HEAD に積まれている）。
  `git log --all -p -S "_FRAGMENTS"` で唯一ヒットするのはこの run 自身の
  `graph.json`（計画テキスト）であり、作業ブランチのコード側に `_FRAGMENTS` を
  作った履歴は無い。

**影響**: t2（codd_gate_*.py 5ファイル調査）・t3（verify.py/mr.py/model.py の結線点特定）・
t4以降（coddgate.py 新規作成・断片統合・`_FRAGMENTS` への登録）はすべて
`kiro_project/` パッケージの存在を前提にしている。ワークツリーがこのまま単一ファイル
構成なら、t3以降は対象ファイル自体が無く成立しない。評価役・後続タスクは次のいずれかの
方針決定が必要（本タスクの範囲外のため実施はしていない）:

1. `main` の `1cee8484` 以降のパッケージ化 refactor をこの作業ブランチへ
   merge/rebase してから coddgate 統合を進める。
2. パッケージ化を諦め、単一ファイル `kiro-project.py` へ直接
   `codd_gate_*.py` の内容を結線する計画に作り直す。

以下は本タスクの担当範囲（`_FRAGMENTS` の構造把握・挿入位置確定・断片規約の記録）を、
**`main` 上に実在するリファレンス実装**を根拠に完了させた内容。ワークツリーへの
書き換えは行っていない（範囲外／対象ファイル不在のため）。

## `_FRAGMENTS` タプルの定義（`main:tools/kiro-project/kiro_project/__init__.py`）

```python
_FRAGMENTS = (
    "_head",       # 共有 import と最下層の定数
    "model",       # Task / enqueue / cohort / intake
    "policy",      # Policy / 自律レベル / パス保護ゲート
    "decisions",   # 決定記録 / DR 学習 / ltm 昇格
    "instances",   # 稼働レジストリ / start・stop・restart
    "state",       # 状態 worktree
    "rules",       # rules.md（恒常ルール）
    "needs",       # 通知・フィードバック / impact・reject
    "prioritize",  # 優先順位 / assess / spec ルーティング / triage
    "verify",      # verify ゲート / verify 合成
    "request",     # 実行要求の組み立て / ルーティング / workspace 解決
    "flow",        # kiro-flow 連携 / act / 委譲 executor
    "config",      # Config / 納品 / journal / settle 補助
    "batch",       # 並列消費 / claims
    "mr",          # タスク MR
    "stategit",    # 状態の git 保存・共有
    "loop",        # 正準ループ run / watch
    "commands",    # 人の操作 / revise / commands 取り込み / stats
    "doctor",      # audit / doctor
    "charter",     # プロジェクト層 / repos / 複数 charter / replan
    "plan",        # repo-map / plan・review / spec 展開
    "gitcache",    # 共有 git キャッシュ + worktree
    "project",     # acceptance / milestone / finalize / cmd_project
    "configfile",  # 設定ファイル解決 / build_config / _add_common
    "update",      # 自動アップデート
    "cli",         # main / サブコマンドのディスパッチ
)
```

命名規則: 全て小文字スネークケース、元の単一ファイル内の記述順（＝機能ブロック順）と
一致させたタプル要素名。各名前は同ディレクトリの `<name>.py` にそのまま対応する
（`pkgutil.get_data(__name__, _name + ".py")` で読み込む）。

合成の実装（要旨）:
- `_g = globals()` で `kiro_project/__init__` 自身の名前空間を取得。
- `_FRAGMENTS` の順に、`pkgutil.get_data` で断片ソースを読み `compile(..., "exec")` → `exec(_code, _g)`。
  全断片が **単一の共有名前空間**（`__init__` の globals）に合成される。
- `pkgutil.get_data` を使う理由はコメントに明記: zipapp（zip内配置）でも動作させるため
  （`open(__file__)` は zip 内で機能しない）。
- 順序が意味を持つ理由: 元ファイルが top-to-bottom で `NameError` なく実行できていた
  という前提を壊さないため。断片間で自由に import せず、後続断片は先行断片が
  globals に置いたシンボルをそのまま参照できる（前方参照は
  `from __future__ import annotations` で型注釈のみ回避、実行順の前方参照は不可）。

## "coddgate" の挿入位置（確定）

**`"prioritize"` の直後・`"verify"` の直前** に1要素として挿入する。

```python
_FRAGMENTS = (
    "_head", "model", "policy", "decisions", "instances", "state", "rules",
    "needs", "prioritize",
    "coddgate",   # ← 挿入位置
    "verify", "request", "flow", "config", "batch", "mr", "stategit", "loop",
    "commands", "doctor", "charter", "plan", "gitcache", "project", "configfile",
    "update", "cli",
)
```

根拠:
- 制約1「`verify` より前」: `verify.py` の回帰ゲート（t3 対象）が `codd_gate_*`
  シンボルを参照するため、`verify` の exec 時点で既に globals に存在している必要がある。
  → 直前に置けば必ず満たす。
- 制約2「依存する既存断片より後」: `coddgate.py` が必要とする既存機能（`codd_gate_base.py`
  が担う「共通データ構造・パス解決・設定/repos.json 読み込み」、および `shutil.which`
  等の標準ライブラリ）は `_head`（共有 import・定数）で足りる可能性が高く、`model`
  （Task構造）・`policy`（自律レベル）を使う可能性も残るため、それらより後に置く必要が
  ある。`verify` の直前に置くことで、それより手前の全断片（`_head` から `prioritize`
  まで）のシンボルが無条件に利用可能になり、依存関係の解決漏れが起きない。
- 実行時参照（`mr.py` の受入判定・`model.py` の enqueue から `codd_gate_*` を呼ぶ話、
  t3 の対象）は関数本体内での呼び出しであり、Python の遅延評価（グローバル参照は
  呼び出し時に解決）によりタプル順が `mr`/`model` より後でも問題ない。実際
  `model` は位置2、`mr` は位置15 でともに `verify`（位置10）と前後関係が割れているが、
  これは元ファイルの記述順をそのまま保存しているだけで、関数内呼び出しの可否とは
  無関係。よって「`verify` より前」という指定だけが実効的な制約であり、直前挿入が
  最も安全かつ指定に忠実な選択。

## 断片規約（`verify.py` 冒頭数行から採取、`main:tools/kiro-project/kiro_project/verify.py`）

```python
from __future__ import annotations
# verify.py — 元 kiro-project.py の 3160-3620 行目（機械分割・内容無改変）。
# 単体 import しない。kiro_project/__init__.py が共有名前空間へ順に exec 合成する。
# verify ゲート / act（kiro-flow 委譲）
# ---------------------------------------------------------------------------
def run_verify(cmd: str, workdir: Path, timeout: float, env: "dict | None" = None) -> "tuple[bool, str]":
    ...
```

具体的な規約:
1. **1行目は必ず `from __future__ import annotations`**（型注釈の遅延評価用。
   前方定義シンボルへの注釈参照が def 時に評価されるのを防ぐ）。
2. **2〜4行目程度にコメントヘッダ**: 元ファイルでの行範囲（機械分割の場合）、
   「単体 import しない／`__init__.py` が共有名前空間へ exec 合成する」という
   自己言及コメント、断片が担う機能の一言要約。`coddgate.py` は機械分割ではなく
   新規作成断片なので「元ファイルの行範囲」コメントは不要（該当しない）が、
   「単体 import しない」ことと機能要約は踏襲すべき。
3. **モジュールレベル import が無い**: `Path`, `os`, `subprocess` などは
   このファイル内で import されていないが普通に使われている＝ `_head` 等
   先行断片が globals に置いた import 済みシンボルにそのまま乗る前提。
   `coddgate.py` も標準ライブラリを使うなら関数内 import にするか、`_head` が
   既に import 済みのものを再 import せず直接使う。
4. **型注釈は文字列リテラルで明示するケースがある**（`"dict | None"`,
   `"tuple[bool, str]"` など）。`from __future__ import annotations` があれば
   本来は素の `dict | None` でも遅延評価されるが、既存断片は念のため文字列化を
   併用しているスタイル。新規断片でもこのスタイルに合わせるのが無難。

## 検証

- 完了条件のシェルコマンド（`pytest -k codd` / `grep -rq codd_gate` /
  `codd-gate verify --strict`）は本タスク（t1 = 調査のみ）の完了条件ではなく、
  run 全体の完了条件。t1 は「ファイルを読み把握し尽くす」ことが完了条件であり、
  ワークツリー内に対象ファイルが存在しないため実コードの読解ではなく `main`
  ブランチ上の同名ファイルを `git show` で参照して代替した（本文中に明記）。
- ワークツリーは一切変更していない（`git status` 差分なし、成果は本レポートのみ）。

## 採用した前提・範囲外で見つけた問題（まとめ）

- 前提: タスク文中の「`tools/kiro-project/kiro_project/__init__.py` を読み」は、
  作業ブランチに実ファイルが無いため、`main` ブランチ上の同一設計のファイル
  （`1cee8484` / `main` HEAD `153e757b`、内容同一）を代替参照先として採用した。
- 範囲外の問題（直さず報告のみ）: 作業ブランチが `main` のパッケージ化 refactor を
  取り込んでいない不整合。t2 以降・run 全体の計画修正が必要になる可能性が高い。
