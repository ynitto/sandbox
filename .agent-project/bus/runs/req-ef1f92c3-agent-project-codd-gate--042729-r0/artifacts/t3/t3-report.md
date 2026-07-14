# t3 成果報告 — .agent/agent-project.yaml 結線 + regression/intake ルーティング実装

**差別化した切り口**: t1 が特定した挿入点（`mr.py:437-438` regression、`model.py:463-` intake）に
実際にパッチを当て、`detect_status()` の検出結果で分岐する no-op 縮退を実装した点（調査・設計ではなく実装・検証）。

## (a) 成果そのもの

対象は `/Users/nitto/Workspace/sandbox`（main ブランチ）ではなく、そこから
`git_worktree.py provision https://github.com/ynitto/sandbox.git --ref main` で取得した
専用 worktree（t1 が指摘した「共有チェックアウトへ直接書き込むリスク」を回避するため）。
差分は本ディレクトリの `codd-gate-routing.patch` に採取済み。変更ファイルは4つ、+42/-2行:

1. **`.agent/agent-project.yaml`**（末尾に追記。既存34行は無変更）
   ```yaml
   regression_cmd: codd-gate verify --base "$KIRO_BASE_REV"
   intake_cmd: codd-gate tasks --debt
   ```

2. **`tools/agent-project/agent_project/_head.py`**（共有 import セクションに追記）
   `codd_gate_status.py`/`codd_gate_base.py` は `agent_project/` パッケージの外（`tools/agent-project/`
   直下）にある独立モジュールのため、`sys.path` にその親ディレクトリを通してから
   `from codd_gate_status import detect_status` / `from codd_gate_base import resolve_base_rev` を
   トップレベル import。`agent_project/__init__.py` の「単一名前空間へ exec 合成」方式により、
   ここで import した2関数は `_head` が最初に exec される（`_FRAGMENTS` の先頭）ため、以降の
   全断片（`mr.py`/`model.py` 含む）の共有 globals から直接呼べる。

3. **`tools/agent-project/agent_project/mr.py`**（`_settle_task` の回帰ゲート）
   - `_codd_gate_regression_ready(cmd)`: `regression_cmd` に `"codd-gate"` を含む場合のみ
     `detect_status().usable` を確認し、未導入なら回帰ゲート自体をスキップ（command-not-found を
     「回帰検知」と誤読しない no-op 縮退）。codd-gate に依らない `regression_cmd`（`make -s smoke` 等）
     は従来どおり常に有効 — 既存挙動を変えない。
   - `_codd_gate_regression_env(cfg, task, venv)`: codd-gate 由来かつ `venv` に `KIRO_BASE_REV` が
     未注入のケース（workspace 未指定タスク等）を `codd_gate_base.resolve_base_rev` で埋める
     （優先順位: 既存 venv > タスクの workspace base ブランチ[`_workspace_spec_for` 経由] >
     `HEAD~1`）。t1 が指摘した「`--base ""` で codd-gate が `_die` する穴」を塞ぐ。
   - 呼び出し箇所を `if ok and not flaky and cfg.regression_cmd:` から
     `... and _codd_gate_regression_ready(cfg.regression_cmd):` に拡張し、実行 env を
     `_codd_gate_regression_env(...)` の戻り値に置き換え。

4. **`tools/agent-project/agent_project/model.py`**（`run_intake` 冒頭）
   `if "codd-gate" in cfg.intake_cmd and not detect_status().usable: return []` を
   `if not cfg.intake_cmd: return []` の直後に追加（既存の早期 return パターンに揃える）。
   `loop.py:576` と `mr.py:529`（`_run_setup`）は両方とも `run_intake(cfg)` を呼ぶだけなので、
   ここ1箇所の修正で両呼び出し元に効く。

## (b) 検証内容と結果

すべて専用 worktree 内（対象リポジトリ本体は無変更）で実行:

- 完了条件の4コマンドをすべて個別に実行 — 全て exit 0:
  - `grep regression_cmd:.*codd-gate verify --base` → マッチ
  - `grep intake_cmd:.*codd-gate tasks` → マッチ
  - `PYTHONPATH=tools/agent-project python3 -c 'detect_status()...'` → `usable=True`
  - `pytest test_codd_gate_detect.py test_codd_gate_routing.py` → 29 passed
- `PYTHONPATH=tools/agent-project python3 -c 'import agent_project as km; ...'` →
  `km.detect_status` / `km.resolve_base_rev` が共有名前空間に正しく合流していることを確認
  （import エラーなし、`km.detect_status().usable == True`）
- 本体の巨大テスト `pytest tests/test_agent_project.py -k "Intake or regression"` → 11 passed
  （既存の `regression_cmd="false"/"true"`、`intake_cmd="printf ..."` 系テストは `"codd-gate"` を
  含まないため `_codd_gate_regression_ready`/intake の新ガードは常に True 側に倒れ、
  挙動が変わらないことを確認済み）
- 同ファイルのフルスイート `pytest tests/test_agent_project.py` → **648 passed, 1 failed**。
  失敗は `TestDaemonRouting.test_kf_base_passes_flow_config`（`/tmp` vs `/private/tmp` の
  symlink 解決差分によるパス比較失敗）で、`git diff --name-only` で確認した通り本タスクの変更
  4ファイルはこのテストの対象コード（`_kf_base`/daemon routing）に触れておらず、
  macOS 環境依存の既存 flaky failure（本タスクと無関係）と判断した。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**:
- t1 の申し送りどおり、`/Users/nitto/Workspace/sandbox`（無関係な大規模差分を抱える共有
  チェックアウト）には書き込まず、`git_worktree.py provision` で取得した専用 worktree
  （`/var/folders/.../kiro-worktree-qpfcd36w`、使用後に release 予定）でのみ編集した。
- 「codd-gate 由来かどうか」の判定は `"codd-gate" in cmd` の文字列包含で行った。
  `configfile.py`/`agent-project.yaml.example` の実値がすべて `codd-gate <サブコマンド>` の形
  であること、他の regression/intake コマンド例（`make -s smoke` 等）が同名を含まないことを
  前提にした軽量ヒューリスティック——正規表現や専用フラグ（`cfg.codd_gate` フィールド新設等）は
  今回のスコープ外と判断（t1 メモの「b1-b3/c1-c2/e1-e2 の cfg.codd_gate フィールド新設は本 run
  の別タスクの責務」という切り分けに整合）。
- `KIRO_BASE_REV` の埋め込み（`codd_gate_base.resolve_base_rev` 経由）は「detect_status() の
  検出結果に応じてコマンドを組み立てる」の一部と解釈し実装した。base rev が無いまま
  `--base ""` を渡すと codd-gate 側が `_die` し、せっかくの検出ベースルーティングが壊れた
  コマンドを組み立てることになるため、スコープに含めるべき前提と判断した。
- `intake_interval` は既定値（`config.py` の 600.0）で足りるため yaml には追記しなかった
  （t1 メモも「完了条件には含まれないため必須ではない」と明記）。

**未解決事項・範囲外で見つけた問題（直していない。報告のみ）**:
- `run_intake` の `subprocess.run(cfg.intake_cmd, shell=True, ...)` はそのまま（argv 化・
  `codd_gate_status.command()` への置き換えはしていない）。`--repos`/`--repo-dir` ルーティング
  引数（`codd_gate_routing.build_routing_args`）の自動付与、`codd_gate_debt.parse_debt_output`
  経由への stdout パース置き換えは t1 メモが「将来の拡張余地」「今回の必須要件ではない」と
  明記した項目のため、範囲外として見送った。
- `agent_project/doctor.py` の `doctor_env_findings` への codd-gate 検出状態の合流（`agent-project
  doctor` からの可視化）は t1 メモ・タスク説明のいずれにも含まれないため未実装（範囲外）。
- `codd_gate_detect.py`/`codd_gate_status.py`/`codd_gate_base.py` の docstring が参照する
  `agent-project.py:XXXX` 行番号の陳腐化（t1 §2 で既指摘）は本タスクでも未修正（範囲外の
  ドキュメント整合性の問題であり、規範「無関係なついで修正をしない」に従い見送った）。
- `TestDaemonRouting.test_kf_base_passes_flow_config` の flaky failure（上記 (b)）は本タスクの
  変更と無関係だが、gate/synth 段階で再現するようであれば別途報告が必要。
