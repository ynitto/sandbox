# t10-codd-gate-wiring-repair — 報告

## (a) 成果

作業対象はメイン worktree（`/Users/nitto/Workspace/sandbox`、branch `main`）。

**調査の結果、t8（タイムアウト）が中断直前までにワーキングツリーへ残していた未コミット差分が、
本タスクの目的（codd-gate 自動検出モジュールを実際の設定生成・更新処理へ接続し、regression_cmd/
intake_cmd の双方を有効化する）をすでに機能面で満たしていることを確認した。** コードの欠落・
NameError・未接続のような「不足」は検出されなかったため、コード変更は行っていない
（存在しない不具合への「ついで修正」を避けた）。確認した接続点は次のとおり:

1. **設定生成への接続**（`tools/agent-project/agent_project/configfile.py`）
   `build_config()`（全サブコマンドが起動時に必ず通る Config 生成経路）の末尾で
   `_apply_codd_gate_auto_wiring(cfg)` を呼び、`codd_gate_wiring.detect_wiring()`
   （実在・バージョン・repos.json schema 互換・capability を実測する a2 相当の glue）の結果から
   `cfg.regression_cmd`/`cfg.intake_cmd` を**メモリ上で**冪等に補う。発火条件は
   「両方とも CLI/設定ファイルに未指定」かつ「`<root>/repos.json` が実在」の場合のみ。
   明示済みの値は既存のまま保持（部分結線・全結線どちらも独立キー単位で成立）。

2. **`.agent/agent-project.yaml` への永続化**（`tools/agent-project/codd_gate_regression.py`、新規）
   `agent-project.yaml` は `state.py` の `_HUMAN_OWNED_STATE_FILES` に含まれる「機械が実行時に
   書き換えない」ファイルのため、(1) はディスクへ書かない設計。ファイルへの実書き込みは
   本モジュールが担当し、`build_regression_cmd()` で codd-gate 検出結果から regression_cmd 文字列を
   組み立て、`upsert_config_text()`/`apply_to_file()` で正規表現ベースの冪等 upsert を行う
   （PyYAML load→dump を使わず既存コメントブロックを保持）。CLI:
   `python3 codd_gate_regression.py --config .agent/agent-project.yaml [--repos <path>] [--dry-run]`。

3. **intake_cmd 側の連携有効化**（`tools/agent-project/agent_project/model.py` の `run_intake`）
   `codd_gate_debt.parse_debt_output()`（sibling module、遅延 import）が使える環境では
   `codd-gate tasks --debt` の stdout をレコード単位で検証し、`title` 欠落など不備な1件だけを
   journal に隔離して残りの取り込みを継続する（1件の不備で intake 全体を止めない）。
   sibling module 欠落時は従来の緩いパースへ no-op 縮退。(1) の自動配線は `intake_cmd` も
   regression_cmd と同じ経路で同時に補う（`recommended_intake_cmd`）。

4. **doctor 連携**（`tools/agent-project/agent_project/doctor.py`）
   `doctor_codd_gate_findings()` が `cmd_doctor()` の決定的所見収集に組み込まれ、
   「codd-gate は使えるが未結線」を info finding として提示する。完全結線済みなら所見なし。

5. **配布（zipapp）**（`tools/agent-project/install.sh`）
   `codd_gate_*.py`（7モジュール）を zipapp ルートへ同梱するよう追記済み。配布バイナリでも
   検出・regression・intake が動く。

6. **ドキュメント**（`docs/designs/codd-gate-design.md` §4.1、`tools/agent-project/README.md`）
   t4 が残した「自動配線は未接続」という記述を、実装後の実際の結線状況（上記1〜5、および
   `.agent/agent-project.yaml` 自体は自動配線で書き換わらない旨）へ更新済み。設計書と実装の
   乖離なし（charter v1 の acceptance「設計書と実装に乖離がない」を満たす）。

## (b) 検証内容と結果

- **完了条件ゲート**: `cd /Users/nitto/Workspace/sandbox && grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml`
  → **exit 0**。`intake_cmd` も同ファイルに `'codd-gate tasks --debt --repos .agent-project/repos.json'`
  として併存を確認。
- **単体テスト**: `python3 -m pytest tools/agent-project/tests/ -q`
  → 750 passed / 1 deselected（`TestDaemonRouting::test_kf_base_passes_flow_config` は
  macOS の `/var` vs `/private/var` シンボリックリンク解決差による**既存の**（本タスクと無関係な）
  失敗であることを diff で確認し除外）。うち `TestCoddGateAutoWiring`（6ケース）・
  `test_codd_gate_wiring.py`・`test_codd_gate_regression.py`・`test_codd_gate_debt.py`
  （計49ケース）が今回の結線ロジックを直接検証し全て通過。
- **実環境の end-to-end 統合確認**（unittest でなく実プロセスでの動作確認）:
  - 空の一時ディレクトリに `repos.json` のみを置いて `build_config()` を実行 →
    同梱の `tools/codd-gate/codd-gate.py` をフォールバック解決で検出し、
    `cfg.regression_cmd = 'codd-gate verify --base "$KIRO_BASE_REV" --repos ./repos.json'`、
    `cfg.intake_cmd = 'codd-gate tasks --debt --repos ./repos.json'` が実際に設定されることを確認。
    `doctor_codd_gate_findings()` は結線済みのため空リスト（想定どおり）。
  - `codd_gate_regression.py` を実プロセスとして起動し、`.agent/agent-project.yaml` 相当のファイルへ
    実際に `regression_cmd` 行を注入 → 完了条件と同じ grep パターンに一致する行が書き込まれることを
    確認。2回目の実行で `changed: false`（冪等性）も確認。
  - `install.sh --prefix <一時ディレクトリ>` を実行し zipapp を生成 → `codd_gate_base.py` /
    `codd_gate_debt.py` / `codd_gate_detect.py` / `codd_gate_regression.py` / `codd_gate_routing.py` /
    `codd_gate_status.py` / `codd_gate_wiring.py` の7モジュール全てが同梱されることを確認
    （実インストール先 `~/.local/bin` には書き込んでいない。一時ディレクトリは検証後に削除済み）。
- **静的チェック**: `git diff --check`（空白エラーなし）、変更・新規ファイル全ての `ast.parse`
  成功（構文エラーなし）。
- **agent-dashboard/agent-flow への影響**: `tools/agent-dashboard`・`tools/agent-flow` 配下で
  `codd_gate`/`codd-gate` を参照する箇所は `agent-dashboard/test/needs-diagnosis.test.js` の
  診断メッセージの例文1箇所のみで、機能結合はない。今回の変更はいずれのソースにも触れておらず、
  既存のエンジン／フロントエンド構成（charter v1 constraints）は維持されている。

## (c) 前提・未解決事項・範囲外の所見

**採用した前提**:
- タスク文の「実際の設定生成・更新処理へ接続」は、全サブコマンドが必ず通る
  `agent_project.configfile.build_config()`（Config 生成＝実質的な「設定生成・更新」の実行点）
  への接続であると解釈した。`.agent/agent-project.yaml` ファイル自体への恒久書き込みは、
  `state.py` の人専有ファイル不変条件（`_HUMAN_OWNED_STATE_FILES`）を尊重し、
  `build_config()` からの自動書き込みではなく明示実行の `codd_gate_regression.py` に委ねる
  設計を正しいものとして踏襲した（t4/t8 双方のドキュメントに明記済みの既定路線）。
- 完了条件の grep 対象行はコミット済みの `.agent/agent-project.yaml`（`b1868483`）に既に存在して
  いたため、本タスクの実質的な作業は「この行を通す自動化コードパスが実在し機能することの確認・
  補完」と解釈した。

**未解決事項**:
- なし。t8 の未コミット差分に不足・破綻箇所は見つからなかった。

**範囲外で見つけた問題（未修正・報告のみ）**:
- `tests/test_agent_project.py::TestDaemonRouting::test_kf_base_passes_flow_config` が
  macOS の `/var`→`/private/var` シンボリックリンク解決により恒常的に失敗する
  （`tempfile.TemporaryDirectory()` の実体パスと `Path.resolve()` の差）。本タスクの変更とは
  無関係な既存の環境依存の失敗であり、修正は範囲外と判断し手を付けていない。
- t4 の報告にあった `schemas/README.md` の旧称「kiro-projects」表記の残存も範囲外のまま
  （未確認・未修正）。
