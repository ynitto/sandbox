# t3: kiro_project/__init__.py の _FRAGMENTS 合成機構と coddgate 挿入位置の確定

## (a) 成果サマリー

### 前提（採用した解釈）
作業ブランチ `kp/kiro-project-codd-gate-171537`（worktree: `/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/kiro-flow-ws-92204-jx2enke7/sandbox`）は `main` の `merge-base` から分岐しており、**このブランチには `tools/kiro-project/kiro_project/__init__.py` がまだ存在しない**（`kiro_project/` 配下にあるのは `coddgate.py` のみ）。`__init__.py` はこのブランチより後に `main` へ入った "kiro-project.py 単一ファイル → パッケージ分割" のリファクタ（コミット `1cee8484`）で追加されたもので、このブランチはそのリファクタを未取り込みである。
このタスクは調査のみでファイルを書き換えない指示のため、`git show main:tools/kiro-project/kiro_project/__init__.py` で読み取った内容を正典として読み解いた（後続タスクがこのブランチへ実装する際は、まず `__init__.py` 一式を main からこのブランチへ持ち込む必要がある——範囲外の問題として (c) に記載）。

### _FRAGMENTS 合成機構の仕組み
`__init__.py` は以下の構造（main 時点、58行）:

```python
_FRAGMENTS = (
    "_head", "model", "policy", "decisions", "instances", "state", "rules",
    "needs", "prioritize", "verify", "request", "flow", "config", "batch",
    "mr", "stategit", "loop", "commands", "doctor", "charter", "plan",
    "gitcache", "project", "configfile", "update", "cli",
)

_g = globals()
for _name in _FRAGMENTS:
    _src = _pkgutil.get_data(__name__, _name + ".py")
    _code = compile(_src, _name + ".py", "exec")
    exec(_code, _g)
```

- 各断片 (`*.py`) は**単体 import されない**。`__init__.py` が `_FRAGMENTS` に列挙した順で `pkgutil.get_data` → `compile` → `exec(code, _g)` を行い、**すべての断片を `__init__` モジュールの globals という単一の共有名前空間へ**合成する。合成後の実行時名前空間は、分割前の単一ファイル `kiro-project.py`（約11,500行）を top-to-bottom で実行したときと完全に同一になる。
- `_FRAGMENTS` の並び＝元の単一ファイルの記述順（＝依存順）。docstring の言葉で言えば「元ファイルが top-to-bottom で NameError なく実行できた以上、この順序を保つ限り import 時の前方参照はすべて満たされる」。

### 断片の書き方の制約（確定事項）
1. **先頭に `from __future__ import annotations` を置く**（`_head.py` 含め全断片で採用済みの規約）。理由: 型注釈を文字列化し、まだ後の断片でしか定義されないシンボルへの型注釈上の前方参照が `def` 実行時（＝そのシンボルより早い段階での exec 時）に評価されて `NameError` になるのを防ぐ。
2. **モジュールレベル（関数本体の外）で他断片のシンボルを参照する場合は、参照先の断片が自分より前に exec 済みでなければならない。** 具体的には次のような箇所が該当する:
   - モジュールトップレベルの定数式・タプル/リスト初期化（例: `_FRAGMENTS` 自体や、`verify.py` にある `_KNOWN_COMMAND_WORDS = frozenset({...})` のような即時評価の式）
   - 関数のデフォルト引数値（`def f(x=other_symbol()): ...` は関数定義＝その断片の exec 時に評価される）
   - デコレータ引数、クラス本体・dataclass のフィールドデフォルト（`default_factory` を使わない場合）など、断片 exec 中に即時実行されるコード
   これらは「後方参照」が許されない——最終的に他ファイルへの `import` を経由しないため、Python の通常のモジュール解決とは異なり、**exec された時点でその名前空間に存在するものしか見えない**。
3. **一方、関数・メソッドの本体内で他断片のシンボルを参照するのは、断片の並び順に関係なく安全**（自分がその断片より後にあっても、前にあっても良い）。理由: 関数本体はコンパイルされるだけで、`_FRAGMENTS` ループの実行中には呼び出されない。実際に呼ばれるのは全断片の exec が完了した後（＝すべてのシンボルが `_g` に揃った後）なので、遅延束縛（late binding、`LOAD_GLOBAL` によるモジュール globals 参照）により解決できる。`model.py`（index 1、`verify` より前）が実行時にしか呼ばれない enqueue 系関数の中で `coddgate` のシンボルを参照できるのはこの性質による。
4. **`_head.py` が最下層**（index 0）であり、共有 import（`shutil`, `dataclasses.dataclass`/`field`, `Path` など）と最下層定数を提供する。新規断片はモジュールレベルで `import` 文を自前で持たず、`_head.py` が提供する共有名前空間の import 済みシンボルにそのまま乗る（`coddgate.py` の先頭コメントに明記された既存規約と一致）。

### "coddgate" の挿入位置が "verify" より前であることの確定
- 確定した位置: `_FRAGMENTS` タプル中、**`"prioritize"` の直後・`"verify"` の直前**（`("_head", ..., "prioritize", "coddgate", "verify", "request", ...)`）。
- 根拠（二重に裏付け）:
  1. **backlog の feedback**（`backlog/kiro-project-codd-gate-171537.md`、9回連続失敗後の評価者コメント）が明示的に指定: 「`kiro_project/__init__.py` の `_FRAGMENTS` に `"coddgate"` を追加する。位置は `"verify"` より前（`verify`/`mr`/`model` から呼ぶため。断片は依存順に exec される）」。
  2. **合成機構の性質と整合**: `verify`/`mr`/`model` の3消費者のうち、`_FRAGMENTS` 順で最も早いのは `model`（index 1）だが、feedback は `model` より前への配置は要求していない——これは `model.py` 側の `coddgate` 参照が enqueue 系の**関数本体内**（実行時遅延解決で安全）に限られる想定である一方、`verify.py`（index 9）側は差分ゲート合成をより即時的な形（回帰ゲートの合否判定に codd-gate の結果を組み込む処理）で行うため、**`verify` の exec 時点で `coddgate` のシンボルが名前空間に存在している必要がある**、という一貫した説明になる。`mr`（index 14）は `verify` よりさらに後ろにあるため、`verify` より前に置けば自動的に `mr` の要件も満たす。
  3. 実装済みの `coddgate.py`（このブランチの `kiro_project/coddgate.py`）自身が `_head.py` 提供の `shutil`/`dataclass`/`field` に依存する旨をコメントで明記しており、`_head`（index 0）より後・`verify`（現行 index 9）より前という挿入位置の制約と矛盾しない。

## (b) 検証内容と結果
- `git show main:tools/kiro-project/kiro_project/__init__.py` で `_FRAGMENTS` 合成機構の実装とdocstringを直接確認（読み取りのみ、チェックアウト・ブランチ切替なし）。
- `git log --all --oneline -- tools/kiro-project/kiro_project/__init__.py` でこのファイルの导入コミット（`1cee8484`, main / `kp/python3--m-pytest-tools--143714` にのみ存在）を特定し、`git branch -a --contains 1cee8484` で作業ブランチ `kp/kiro-project-codd-gate-171537` に未到達であることを確認。
- `git show main:tools/kiro-project/kiro_project/_head.py` / `verify.py` を読み、`_head.py` が `shutil`/`dataclass`/`field` を import 済みであること、`verify.py` にモジュールレベルの即時評価コード（例: `_KNOWN_COMMAND_WORDS` frozenset）が存在することを確認——「モジュールレベル参照は前方定義不可」という制約の具体例として妥当。
- 作業ブランチの `kiro_project/coddgate.py`（既存）を読み、内部コメントが「`_head.py` の import に依存」「単体 import 不可・`__init__.py` の `_FRAGMENTS` 経由 exec 前提」と明記しており、上記合成機構の理解と一致することを確認。
- 本タスクは調査のみのため、完了条件のシェルコマンド（pytest / grep / `codd-gate verify`）は本タスクの実行対象外（`__init__.py` 自体が未作成のため実行しても意味のある結果にならない）。この確定は次タスク（t4 以降、`__init__.py` の新規作成・`_FRAGMENTS` 更新を担う想定）が使う設計事実であり、その回で完了条件のコマンドが満たされる。

## (c) 前提・未解決事項・範囲外で見つけた問題
- **前提**: 上記の通り、`__init__.py` は作業ブランチに存在しないため main 版を正典として読み解いた。次タスクが `__init__.py` を新規に持ち込む際は、main の現行版（`_head`〜`cli` の26断片）をベースに `"coddgate"` を `"prioritize"` と `"verify"` の間へ挿入する形が妥当。
- **範囲外で見つけた問題（このタスクでは変更していない）**:
  - `tools/kiro-project/codd_gate_base.py` / `codd_gate_debt.py` / `codd_gate_detect.py` / `codd_gate_routing.py` / `codd_gate_status.py` が `kiro_project/` パッケージの**外側**に残存している。backlog feedback 通り、これらは `coddgate.py` へ統合・削除が必要（9回連続失敗の直接原因）。
  - `tools/kiro-project/tests/test_codd_gate_*.py`（4ファイル）が上記の外側ファイル群を前提にしている可能性が高く、`coddgate.py` への統合後にインポート先を揃える必要がある。
  - `tools/kiro-project/kiro-project.py`（単一ファイル、main のリファクタでは撤去されている）が作業ブランチにまだ残っている。パッケージ化と二重管理になっていないか、次タスクで要確認。
