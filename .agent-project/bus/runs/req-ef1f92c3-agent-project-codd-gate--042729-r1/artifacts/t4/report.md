# t4: codd-gate detect / routing テスト作成

**差別化の切り口**: 既存の `test_codd_gate_detect.py`/`test_codd_gate_routing.py`
（前ラウンド由来、29件PASS）はタスク文面の要求と完全一致していなかった2点のギャップを
埋める最小追加で完了条件を満たす方針にした（ファイル新規作成ではなく既存資産の補完）。

## (a) 成果

対象は `/Users/nitto/Workspace/sandbox`（backlog `workspace: sandbox`。t1 契約に準拠）。
`tools/agent-project/tests/test_codd_gate_detect.py`・`test_codd_gate_routing.py` は
前ラウンドで実装済み・29件PASSだったが、タスク文面が明示する2点が未カバーだったため
最小差分で追加した（新規作成ではなく既存ファイルへの追記。差分は各ファイル末尾に
`git status --short` で確認済み、2ファイルのみ変更）。

**追加1（detect側）**: `command('verify', '--base', 'HEAD')` の生成結果を直接検証する
`test_command_builds_verify_base_head_argv` と、実 `shutil.which`（PATH を空にする
`os.environ` パッチ）を使う `test_empty_path_env_with_real_which_degrades_to_noop` を
`TestCoddGateStatusNoOpDegradation` に追加。既存の `test_cli_absent_degrades_to_noop` は
`which=lambda _name: None` というスタブで「PATH 上に無い」を決定的に模したものだったが、
今回は実 `shutil.which` を使い `PATH=""` にする経路も追加し、両方の意味で
「未インストール系（PATH を空にして usable=False かつ非例外）」を担保した
（同梱パス `tools/codd-gate/codd-gate.py` はこの sandbox に実在するため、`Path.exists` を
モックしないと「同梱フォールバックで usable=True」に転んでしまう点に注意——この2つの
経路をどちらも塞いで初めて「真に未検出」になる、という compose の構造を明示的に書いた）。

**追加2（routing側）**: `test_codd_gate_routing.py` に `TestAgentProjectYamlWiring` を追加。
`.agent/agent-project.yaml`（一時ディレクトリ配下に実体を作る）へ `codd_gate_routing.build_routing_args`
が組み立てる `--repos`/`--repo-dir` を埋め込んだ `regression_cmd`/`intake_cmd` を
`yaml.safe_dump` で書き込み、`yaml.safe_load` で読み戻して
`regression_cmd` が `codd-gate verify --base` パターンへ、`intake_cmd` が `codd-gate tasks`
パターンへルーティングされることを検証する。

## (b) 検証内容と結果

- `PYTHONPATH=tools/agent-project python3 -m pytest tools/agent-project/tests/test_codd_gate_detect.py tools/agent-project/tests/test_codd_gate_routing.py -q`
  → **32 passed**（既存29件 + 追加3件）。
- PYTHONPATH 無しでも同じコマンドが通ることを確認済み（両テストファイルが自前で
  `sys.path.insert(0, ...)` するため。backlog の完了条件シェルは `PYTHONPATH=` を
  `python3 -c` の1コマンドにしか適用しないシェル構文のため、この独立性を確認する意味があった）。
- `PYTHONPATH=tools/agent-project python3 -c 'from codd_gate_status import detect_status; s=detect_status(); assert s.usable and s.command("verify", "--base", "HEAD")'`
  → 実環境（PATH 上に `codd-gate` あり）で成功（exit 0）。
- `git status --short` （sandbox）→ 変更ファイルは対象2ファイルのみ。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 対象コードは t1 契約と同じく `/Users/nitto/Workspace/sandbox` とした
  （本 worktree `.agent-project` には `tools/agent-project` が存在しない）。
- **前提**: routing 側の新テストは `agent_project` パッケージ（`configfile.py` の
  config loader）を経由せず、素の YAML 読み書きで `.agent/agent-project.yaml` の内容を
  検証する設計にした。理由: `agent_project/__init__.py` は26断片を1つの共有名前空間へ
  `exec` 合成する重量級インポートで、`tests/test_agent_project.py` のコメントが記録する
  通り「cwd の実設定ファイルを拾って実リポジトリへ誤コミットする」実害が過去に起きている
  （2026-07-11）。実際の `cfg.regression_cmd`/`cfg.intake_cmd` への自動配線（b3/c1/e1）は
  t1 契約が明記する通り別タスクの担当で、現状の `.agent/agent-project.yaml`（sandbox）は
  まだこの2キーを持たない。よって「実ファイルを読む」テストにすると他タスクの完了に
  依存して落ちる可能性があったため、一時ディレクトリに構築した等価な fixture を読む形にし、
  本タスク単体で常に green になるようにした。
- **未解決事項**: `.agent/agent-project.yaml` への実際の `regression_cmd`/`intake_cmd` 追記、
  および `agent_project/mr.py`・`agent_project/model.py`・enqueue 経路への自動配線
  （b3/c1/e1）は本タスクの範囲外（他タスク・後続ラウンドの担当）。
- **範囲外で見つけた問題**: 無し（前回 t1 が報告した「t2 ゴール文面とcodd_gate_status.py実装済みの重複」は既知事項として据え置き、本タスクでは追加調査していない）。
