# t2 成果報告 — regression 結線（regression_cmd 生成・注入の仕組み）

## (a) 成果そのもの

新規モジュール `tools/agent-project/codd_gate_regression.py`（+ 単体テスト
`tools/agent-project/tests/test_codd_gate_regression.py`、20 tests）を実装した。
既存の `codd_gate_detect.py`（a1）／`codd_gate_status.py`（a4）／`codd_gate_routing.py`（b2）
と同じ「標準ライブラリのみ・純粋関数中心・単体テスト容易性優先」の流儀に揃えた sibling module。

差分は本ディレクトリの `codd-gate-regression.patch`（2ファイル、+401行、無修正で
`main` HEAD `d214fe9f` に `git apply` 可能・適用後 pytest 52 passed を確認済み）。
`codd_gate_regression.py` / `test_codd_gate_regression.py` の単体ファイルも同梱。

**責務**（README.md「一貫性ゲート（codd-gate 連携）」節が明記する『有効化は設定だけ』方針に
従い、runtime hook ではなく静的な設定ファイル生成・注入に限定）:

1. `build_regression_cmd(status, repos_path, base='"$KIRO_BASE_REV"')` — `CoddGateStatus.usable`
   が False（未検出・バージョン不適合・schema 不適合のいずれか）なら `None`（no-op 縮退）。
   usable なら `'codd-gate verify --base "$KIRO_BASE_REV" --repos <repos_path>'` を返す。
   **`status.binary`（PATH 解決済みの絶対パス、または同梱パス実行用の `[sys.executable, path]`）は
   埋め込まない**——共有設定ファイルへ環境固有の絶対パスを焼き込むと別マシンで壊れるため、
   常に固定の `codd_gate_detect.BINARY_NAME`（"codd-gate"）を使い、実行時の PATH 解決に委ねる
   （このリポジトリ全体の規約と一致）。`status.usable` は可否判定にのみ使う。
2. `upsert_config_text(text, cmd, key="regression_cmd")` — agent-project.yaml の生テキストへの
   冪等な行編集。PyYAML の load→dump は使わない（既存ファイルの手書きコメントブロックが
   ラウンドトリップで失われるため）。正規表現で対象キーの1行だけを差し替える／挿入する。
3. `apply_to_file(yaml_path, cmd)` — 実ファイルへの反映。変更が無ければ書き込み自体を省略
   （mtime を無用に更新しない）。
4. CLI（`python3 codd_gate_regression.py --config .agent/agent-project.yaml [--repos ...] [--dry-run]`）
   — detect_status → build_regression_cmd → upsert を1コマンドで実行し、JSON で結果を報告する。

**スキーマと挿入位置の担保**:
- 挿入位置は `intake_cmd:` があればその直前（既存の「一貫性ゲート」ブロックの一員）、
  無ければ `agent_cli:`（グローバル既定ブロック）の直前に見出しコメント付きの新規ブロックとして、
  どちらも無ければファイル末尾に見出しコメント付きで追記——実在の `.agent/agent-project.yaml`
  の並び（`root:` → 一貫性ゲートブロック → グローバル既定）と一致する。
- 冪等性: 既存値と生成値が一致すれば無変更（`changed=False`）。異なれば該当1行だけを置換し、
  他のキー・コメントは一切変更しない。キーが重複挿入されないことをテストで担保。

## (b) 検証内容と結果

- `python3 -m unittest`／`pytest` で新規 20 tests 実行 → **全て pass**。既存の
  `test_codd_gate_detect.py`（29件）・`test_codd_gate_routing.py`（残り）と合わせて
  `pytest tools/agent-project/tests/test_codd_gate_{regression,detect,routing}.py` →
  **52 passed**。
- **実ファイルに対する冪等性の実地検証**: `git_worktree.py provision` で取得した使い捨て
  worktree（`main` HEAD、対象リポジトリ本体は無変更）の `.agent/agent-project.yaml`
  （既に `regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'`
  を持つ）に対し、CLI を3回連続実行 → 全て `"changed": false`、ファイルは1バイトも変化せず
  （`diff` で確認）。
- **新規挿入の実地検証**: `regression_cmd`/`intake_cmd` を持たない最小 yaml（`root:` +
  `agent_cli:`/`model:` のみ）に対して実行 → 見出しコメント付きで `agent_cli:` の直前に
  正しく挿入され、`grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base'` が exit 0。
- **完了条件との直接照合**: 生成したテキストが完了条件のシェルコマンド
  （`grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base'`）に一致することを
  テスト（`test_generated_line_satisfies_completion_condition`）と実地の両方で確認。
- **パッチ適用の独立検算**: 別途 `git_worktree.py provision` で取得した使い捨て worktree
  （`main` HEAD `d214fe9f`）に `codd-gate-regression.patch` を `git apply --check` →
  成功、実際に `git apply` → 成功、適用後 pytest 52 passed・完了条件 grep exit 0 を確認。
  対象リポジトリ本体（共有チェックアウト）・sparse checkout のいずれにも書き込んでいない。
- 現在の `main` の `.agent/agent-project.yaml`（本タスク開始前から既に `regression_cmd`/
  `intake_cmd` の静的な値を保持済み）は本タスクでは一切変更していない（前提1を参照）。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**:
- タスク文言「検出結果に応じて…生成・注入する仕組みを用意する」は、
  `tools/agent-project/README.md`「一貫性ゲート（codd-gate 連携）」節が明記する
  『有効化は設定だけ』という既存方針（runtime hook 不要、静的な `regression_cmd:` 一行で完結）
  に沿う**生成・注入ツール**の実装と解釈した。他の codd_gate_status.py 等の docstring が言及する
  `cfg.regression_cmd` への実行時自動配線（"b3" ラベル）は、README の方針と重複する別設計
  （かつ r0 で試みられたが `main` へは未統合）と判断し、今回のスコープには含めなかった。
- `main` の `.agent/agent-project.yaml` は本タスク開始時点で既に完了条件を満たす静的な
  `regression_cmd`/`intake_cmd` を持っていた（誰がいつ追加したかは履歴上 `f4660b04` だが、
  本タスクの成果ではない）。このため実ファイルへの書き込みは行わず、**専用 worktree
  上での適用・検証のみ**に留めた——共有チェックアウトを汚さない規約と、「実際に有効化する」
  のは t6（synthesize）の責務という run のタスク分割（t2=仕組みの実装、t6=適用・有効化）
  に従った。
- `--repos` の既定値は `.agent-project/repos.json`（実在の yaml と一致）とし、CLI では対象
  yaml の `root:` から `<root>/repos.json` を推定するフォールバック（`infer_default_repos_path`）
  を追加した——README.md の「repos.json は `<root>/repos.json` に自動生成される」規約に
  合わせるための拡張で、完了条件には含まれないが「スキーマを担保する」というタスク文言に
  対応する前提として採用した。
- `--repo-dir` は含めない前提とした。既存の実 yaml・`codd_gate_routing.py` の docstring
  （regression_cmd はプロジェクトルート自身で実行され、repos.json の `dir` で足りる）と
  一致することを確認済み。

**未解決事項・範囲外で見つけた問題（直していない。報告のみ）**:
- 生成した `codd_gate_regression.py` を実際に呼び出す配線（`agent-project doctor` からの
  実行、CI hook 化、または t6 の統合パッチへの組み込み）は本タスクのスコープ外——
  「仕組みを用意する」までがタスク文言で、呼び出し元の配線は t6（synthesize）／t5（統合前検証）
  の担当と判断した。
- `codd_gate_status.py`/`codd_gate_base.py`/`codd_gate_detect.py` の docstring が参照する
  `.agent-project/bus/runs/run-20260712-213419-5922/artifacts/...` は現在このリポジトリの
  どちらの worktree にも実在しない（過去 run の成果物が既に掃除された可能性）。参照切れの
  ドキュメント整合性の問題だが、無関係なついで修正をしない規範に従い未修正のまま報告する。
- t3（intake 結線）が並行して `/Users/nitto/Workspace/sandbox`（共有チェックアウト）の
  `model.py`/`codd_gate_debt.py`/README 等を直接編集中（未コミット）であることを確認した。
  ファイル的に本タスクの変更とは重複しないため衝突は無いが、共有チェックアウトへの直接
  書き込みが観測された旨は範囲外の事実として申し送る（t3 側の作業規約の是非は評価しない）。
- intake_cmd 側の生成・注入（対称のツール）は t3 の担当のため実装していない。
