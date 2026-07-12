# tools/kiro-project/tests 構成調査 と `-k first_command_line` 選択確定

対象: worktree `kp/synth_verify-_first_comm-172544`（HEAD `9fcf0e9`）、
`tools/kiro-project/tests/`

## ディレクトリ構成

```
tools/kiro-project/
├── kiro-project.py          # 実装（テスト対象）
└── tests/
    └── test_kiro_project.py # テスト唯一のファイル（7826行、69クラス）
```

- `conftest.py` は存在しない（リポジトリ内に conftest.py が置かれているのは無関係な
  `.github/skills/table-spec-extractor/tests/` `.github/skills/spec-value-finder/tests/` の2箇所のみ）。
- `pytest.ini` / `pyproject.toml` / `setup.cfg` 等、kiro-project 用の pytest 設定ファイルも無い。
  pytest はデフォルト探索（`test_*.py` / `Test*` / `test_*`）で本ファイルを拾う。
- テストは `python -m unittest discover -s tools/kiro-project/tests` でも
  `python3 -m pytest tools/kiro-project/tests` でも同一ファイルが実行される
  （モジュール docstring に unittest discover コマンドが明記されている）。

## 命名規約

- ベースクラス: `unittest.TestCase`（pytest 固有の fixture 機構は不使用）。
- クラス名: `Test<機能名>` または `<機能名>Tests`（例: `TestVerifyAssist`,
  `SelfUpdateTests`, `RiskDigestTests`）の 2 系統が混在。新規機能追加時は直近の
  `TestXxx` 系が優勢（69クラス中大半）なのでそちらに合わせるのが無難。
- メソッド名: `test_<snake_case で対象+条件+期待結果>`。例:
  `test_first_command_line_skips_blank_and_comment_lines_inside_fence`
  のように「関数名 + 状況 + 挙動」を1メソッド1アサーション相当で表す。
- 対象関数へのアクセスはグローバル `km`（`kiro-project.py` を
  `importlib.util.spec_from_file_location` でロードしたモジュールオブジェクト、
  L47-51）経由。プライベート関数（`_` prefix）も `km._first_command_line(...)` の形で
  直接呼べる。

## フィクスチャ / セットアップ

pytest fixture・`setUp`/`tearDown` は使わず、モジュールレベルのヘルパー関数を
各テストが明示的に呼ぶスタイル。代表例:

- `cfg_for(d: Path, **kw) -> Config`（L63-）: `tempfile.TemporaryDirectory()` 配下に
  最小構成の `Config` を作る。ほぼ全テストがこれを使う。
- `mkb(d, tid, status=..., verify=..., ...)`（L54-62）: backlog タスクファイルを1件書く。
- 局所ヘルパー（特定クラス群専用）: `_submit_feedback`, `_seed_learn`, `_seed_hits`,
  `write_charter`, `_drained`, `_write_backlog_task`, `_make_skill_repo`,
  `_commit_change` など、使用箇所の近くにモジュールレベル関数として定義。
- モジュール読み込み時（ファイル先頭 L26-45）でテスト全体に共通する副作用の分離を実施:
  GPG署名を無効化する環境変数の上書き、`KIRO_SKILL_REGISTRY` を存在しないパスに固定、
  実リポジトリへの誤コミット事故（2026-07-11 発生）を防ぐため `os.chdir` で中立な一時
  cwd に退避。個々のテストは各自 `tempfile.TemporaryDirectory()` で使い捨てディレクトリ
  を作る（テスト間の共有状態なし）。

## `_first_command_line` 関連テストの現況

`TestVerifyAssist` クラス（L4864-5063）内に既に 13 個の `test_first_command_line_*`
系メソッドが存在し、うち `-k first_command_line` にマッチするのは以下 12 件
（`test_first_command_line_treats_unclosed_fence_as_running_to_end` は
`_code_fence_lines` の直接アサーションも含むが名前に `first_command_line` を含むため
同時にヒットする）:

```
$ python3 -m pytest tools/kiro-project/tests -q -k first_command_line --collect-only
tools/kiro-project/tests/test_kiro_project.py::TestVerifyAssist::test_first_command_line_accepts_path_and_hyphenated_cli
tools/kiro-project/tests/test_kiro_project.py::TestVerifyAssist::test_first_command_line_extracts_all_fence_lines_in_order
tools/kiro-project/tests/test_kiro_project.py::TestVerifyAssist::test_first_command_line_prose_only_never_becomes_synth_verify_command
tools/kiro-project/tests/test_kiro_project.py::TestVerifyAssist::test_first_command_line_returns_command_from_bash_fence_after_prose
tools/kiro-project/tests/test_kiro_project.py::TestVerifyAssist::test_first_command_line_returns_direct_command
tools/kiro-project/tests/test_kiro_project.py::TestVerifyAssist::test_first_command_line_returns_none_for_prose_only
tools/kiro-project/tests/test_kiro_project.py::TestVerifyAssist::test_first_command_line_returns_none_without_candidate
tools/kiro-project/tests/test_kiro_project.py::TestVerifyAssist::test_first_command_line_skips_blank_and_comment_lines_inside_fence
tools/kiro-project/tests/test_kiro_project.py::TestVerifyAssist::test_first_command_line_skips_language_tag_remnant_inside_fence
tools/kiro-project/tests/test_kiro_project.py::TestVerifyAssist::test_first_command_line_skips_unfenced_prose_before_command
tools/kiro-project/tests/test_kiro_project.py::TestVerifyAssist::test_first_command_line_skips_unpunctuated_english_prose
tools/kiro-project/tests/test_kiro_project.py::TestVerifyAssist::test_first_command_line_treats_unclosed_fence_as_running_to_end

12/524 tests collected (512 deselected) in 0.03s
```

実行結果:

```
$ python3 -m pytest tools/kiro-project/tests -q -k first_command_line
............                                                             [100%]
12 passed, 512 deselected in 0.06s
```

完了条件コマンド `python3 -m pytest tools/kiro-project/tests -q -k first_command_line`
は本タスク着手前から成功している（実装 `_first_command_line` / `_code_fence_lines` /
`_first_executable_line` は既にコードフェンス対応済み。詳細実装調査は
`artifacts/t1/synth_verify_first_command_line_investigation.md`、契約整理は
`artifacts/t2/first_command_line_contract.md` を参照）。

## 新規テストを追加する場合の置き場所・命名の決定

以後 `_first_command_line`（および補助関数 `_code_fence_lines`,
`_first_executable_line`, `_has_command_like_leading_token`）に関するテストを
追加する場合の規約:

- **置き場所**: `tools/kiro-project/tests/test_kiro_project.py` の
  `TestVerifyAssist` クラス（L4864 開始、`_first_command_line` 系は L4926-4990 付近）
  に追記する。新規ファイル・新規 conftest は作らない（本テストスイートは単一ファイル
  運用が一貫した規約のため）。
- **命名**: `test_first_command_line_<状況を表す動詞句>` の形式
  （例: `test_first_command_line_skips_<condition>`,
  `test_first_command_line_returns_<expected>`,
  `test_first_command_line_treats_<edge_case>`）。`-k first_command_line` で
  確実に選択されるよう、対象関数名 `first_command_line` をメソッド名に含めることが
  必須（`_code_fence_lines` 単体の新規テストも、この対象関数の内部実装であるため
  同一プレフィックスに揃えるのが既存踏襲と一貫する）。
- **フィクスチャ**: 純粋関数群（`_first_command_line` 等）は `Config` や一時ディレクトリ
  を必要としないため、既存の `test_first_command_line_returns_direct_command` 等と同様
  `km._first_command_line("...")` を直接呼ぶだけでよい。`cfg_for` / `mkb` は
  `synth_verify` 経由の結合テスト（`test_synth_verify_strips_ansi_from_kiro_output` 等）
  でのみ必要。

## 検証

- `python3 -m pytest tools/kiro-project/tests -q -k first_command_line --collect-only`
  で対象 12 件が選択されることを確認。
- 同コマンドを `--collect-only` なしで実行し `12 passed, 512 deselected` を確認
  （完了条件を満たすコマンドは既に成功する状態）。
- `git status --short` は空（作業ツリーはクリーン、HEAD `9fcf0e9`）— 本タスクは調査のみで
  ファイルを一切変更していない。

## 採用した前提・範囲外の所見

- 「新規テストの置き場所と命名を決める」というタスク文面から、実際に新規テストを
  追加するのではなく **規約を確定して報告する** ことが本タスクのスコープと解釈した
  （元要求の実装修正・テスト追加そのものは他タスク `t1`/`t2` および先行コミット
  `9fcf0e9` が既に完了させている）。
- 範囲外の所見: 本 run 内の並行タスク `t1`・`t2` も同じ対象関数を調査しており、
  実装は着手前から完了済みという同一の事実に到達している。三者の報告に矛盾はない。
  重複調査が発生している点は run 全体のタスク分割（評価役）側で把握しておくとよい。
