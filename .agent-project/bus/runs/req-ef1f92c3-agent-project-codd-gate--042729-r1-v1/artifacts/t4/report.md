# t4 — codd-gate 自動検出・regression/intake 結線ドキュメント整理

## (a) 成果

編集対象はいずれもメイン worktree（`/Users/nitto/Workspace/sandbox`、branch `main`）。

1. **`docs/designs/codd-gate-design.md`**（唯一の設計正典）— §4 の末尾に新設「### 4.1 自動検出レイヤ
   （`tools/agent-project/codd_gate_*.py`）」を追加。
   - `codd_gate_detect.py` / `codd_gate_status.py` / `codd_gate_routing.py` / `codd_gate_base.py` /
     `codd_gate_debt.py` の5モジュールを表で整理し、各々の責務と主要関数・型を明記。
   - データ契約: 入力（`schemas/repos.schema.json` 準拠 `repos.json`）・出力
     （`schemas/task.schema.json` 準拠の `tasks --debt` stdout）・`CoddGateStatus` の no-op 縮退則
     （usable/command()/findings の短絡順）を整理。
   - **現在地（結線状況）**を明記: 上記5モジュールは単体テスト付きの部品として実在するが、
     `agent-project.py` 本体（回帰ゲート=`mr.py`／`run_intake`=`model.py`）からは未 import で
     自動配線は未接続。現時点の有効化手順は既存の①〜③表（`regression_cmd`/`intake_cmd` への
     手書き文字列）のみであることを明示し、codd-gate 未インストール環境で手動設定した場合の
     実際の挙動（regression 側は「回帰検知」として全タスク block／intake 側は journal 記録のみで
     無害）を根拠付きで記載。
   - 冒頭の最終更新日を 2026-07-15 に更新。
2. **`tools/agent-project/README.md`** — 既存の「一貫性ゲート（codd-gate 連携・オプション）」節に
   4行追記。自動検出部品の存在と未結線の事実、詳細は codd-gate-design.md §4.1 参照、を一言で
   案内（README 側は簡潔さを優先し、詳細は正典へ委譲）。

## (b) 検証内容と結果

- 完了条件ゲート: `cd /Users/nitto/Workspace/sandbox && grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml` → **exit 0**（既存設定のまま。今回の編集はドキュメントのみで
  この設定行には触れていない）。
- 追記した全てのコード内パス参照（`codd_gate_detect.py` 等5モジュール、`tests/test_codd_gate_detect.py`、
  `tests/test_codd_gate_routing.py`、`schemas/repos.schema.json`、`schemas/task.schema.json`）を
  `Read`/`find` で実在確認済み。
- 追記した挙動の記述（regression 失敗時の block／intake 失敗時の無害化、`resolve_base_rev` の
  フォールバック順、`CoddGateStatus` の判定順）は `mr.py:437-447`・`model.py:463-489`・
  `codd_gate_status.py:91-116`・`codd_gate_base.py:32-54` を直接読んで裏取り。
- `python3 tools/codd-gate/codd-gate.py verify --base HEAD` を試走。`--repos` 未指定（このリポジトリに
  `repos.json` が未生成のため）の簡易実行では大量の AMBER が出るが、これは今回の編集と無関係な
  **既存の debt**（`../codd-gate/README.md` など元々存在する相対リンクも同様に「解決できない」と
  出る＝`--repos`/`--repo-dir` を渡さない単純実行では正しく解決できない環境依存のノイズ）と判断。
  今回の追記範囲（`codd-gate-design.md` §4.1 全体、`README.md` の追記4行）に絞って該当行番号を
  フィルタしたところ、`README.md` 行240の1件のみが該当したが、参照先 `../../docs/designs/codd-gate-design.md`
  は実在するファイルであり、同じ簡易実行では既存の正しいリンク（例: 行231 `../codd-gate/README.md`）も
  同様に誤検知されている——今回の追記が新規に生んだ壊れた参照ではないと判断した。

## (c) 前提・未解決事項・範囲外の所見

**採用した前提**:
- 完了条件（グレップ対象の yaml 行）は既に満たされていたため、t4 の実質的な完了条件は
  「タスク定義の goal（ドキュメント整理）を満たすこと」と解釈した。
- t1〜t3（自動検出ロジック実装・regression結線・intake結線の実装）は本タスクの範囲外（並行タスク）
  のため、コード（`tools/agent-project/agent_project/*.py`、`codd_gate_*.py`）は一切変更していない。
  ドキュメントは**現状のコード**（自動検出モジュールは存在するが未結線）をありのまま記述した。
  t1〜t3 が今後 `cfg.regression_cmd`/`cfg.intake_cmd` への自動配線を実装した場合、§4.1「現在地」の
  段落は更新が必要になる（設計自体・データ契約の記述は変わらない想定）。

**範囲外で見つけた問題（未修正・報告のみ）**:
- `schemas/README.md` が結合先ツールの所有者表記に旧称「kiro-projects」を使い続けている
  （`agent-tools-rename-design.md` によれば agent-project/agent-flow/agent-dashboard の移行は完了済み）。
  ドキュメント間の用語不整合の可能性があるが、本タスクの対象外（codd-gate 連携ドキュメントではなく
  スキーマ命名の一貫性の話）のため未修正。
- `.agent-project/repos.json`（sandbox ルート）が現時点で未生成。charter からの自動生成タイミング次第
  で正常動作の範囲内だが、`regression_cmd`/`intake_cmd` を実際に実行する際は生成済みか確認が要る。
