# t5: `_first_command_line` 修正（t4 指摘の取り込み）

## 前提
- 実コードは `docs/agent-project-verify-location` の運用どおり、`.agent-project`（sparse 制御面）ではなく
  メイン worktree `/Users/nitto/Workspace/sandbox` の `tools/agent-project/agent_project/verify.py` にある。
  完了条件コマンドの `PYTHONPATH=tools/agent-project` はこの worktree で評価。
- 作業開始時点で working tree の `verify.py` は `_strip_leading_command_label` 機能ごと欠落しており
  （直近コミット時点には存在した実装・テストが working tree 側にだけ無い状態）、t4 の指摘は
  「機能はあるが2ケースだけ漏れる」ではなく「機能自体が丸ごと無い」状態からの再実装として対応した。
- 反復は1回で収束（初回実装が完了条件・t4 の全敵対ケースを満たした）。

## 反復ログ
1. 初期状態: `_strip_leading_command_label` が working tree に存在せず、同一行ラベル形式
   （`検証コマンド: <command>`）はフォールバック抽出で先頭トークン判定に落ちる。
   → t4 指摘の2ケース（二重ラベル・散文前置き）を含めて成立するよう再実装。

## 変更内容
`tools/agent-project/agent_project/verify.py`
- `_VERIFY_COMMAND_LABEL_RE` を `^検証コマンド\s*[:：]\s*`（行頭固定・1回限り）から
  `^.*?検証コマンド\s*[:：]\s*`（行内どこにあってもラベルまでを最短一致で剥がす）へ変更。
- `_strip_leading_command_label` を「変化がなくなるまで繰り返し `sub(count=1)`」に変更し、
  二重・多重ラベルも収束するまで剥がす。
- `_first_executable_line` / `_first_command_line` のフォールバック抽出の両方に
  `_strip_leading_command_label` を適用（既存の呼び出し位置を踏襲）。
- docstring を更新し、前置き散文・二重ラベル対応を明記。

## 検証
- 完了条件コマンド:
  ```
  PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_REV\""'
  ```
  → **exit 0**。
- t4 の指摘した不合格ケース（両方とも修正後は期待どおり）:
  - `検証コマンド: 検証コマンド: codd-gate verify --base "$KIRO_BASE_REV"` → `codd-gate verify --base "$KIRO_BASE_REV"`
  - `以下を実行してください。検証コマンド: codd-gate verify --base "$KIRO_BASE_REV"` → `codd-gate verify --base "$KIRO_BASE_REV"`
- t4 が既に確認済みだったケース（回帰なしを再確認）:
  ラベルのみ別行、全角コロン、コードフェンス内（フェンス内同一行ラベルも含む）、
  末尾空白、二重引用符内のコロン（`git commit -m "note: fix bug"`）、素のコマンド行、
  候補なし（`None`）— 全て期待どおり。
- 既存テストスイート `tools/agent-project/tests/test_agent_project.py` を全件実行
  （667 件中 666 pass / 1 fail）。唯一の失敗 `TestDaemonRouting.test_kf_base_passes_flow_config` は
  macOS の `/var` → `/private/var` シンボリックリンク解決差によるもので、本タスクの変更（`verify.py`）
  と無関係かつ本タスク着手前から存在する既知の環境差分（範囲外）。

## 未解決事項・範囲外で見つけた問題
- working tree の `test_agent_project.py` から、以前 `_strip_leading_command_label` 用に存在していた
  3件のユニットテスト（`test_first_command_line_strips_japanese_label_on_command_line` 等）が
  working tree 上で消えていた（HEAD には存在）。テストファイルの追加・復元は本タスクのスコープ
  （`_first_command_line` の修正）外のため着手していない。テストが必要なら別タスクとして
  `_strip_leading_command_label` の3ケース＋t4 の2ケースをカバーするテスト追加を推奨。
- `TestDaemonRouting.test_kf_base_passes_flow_config` の macOS パス正規化起因の失敗は範囲外
  （`verify.py` 非依存）。
- working tree にはこの他にも `configfile.py` / `doctor.py` / `model.py` / `codd_gate_debt.py` /
  `install.sh` / README / docs 等の未コミット変更が既に存在していたが、いずれも本タスクの
  対象外につき触れていない。
