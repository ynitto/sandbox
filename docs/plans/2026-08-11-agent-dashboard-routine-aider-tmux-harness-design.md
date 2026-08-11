# Agent Dashboard: Aider ステートマシン実行の tmux 化と agent-loop ハーネス設計

## 背景

[同日の設計](2026-08-11-agent-dashboard-routine-tier-aider-statemachine-design.md)で、
定型業務（statemachine）の Aider 実行は dashboard 管理の in-process 実行器
（`stateMachineRunner.js`）になった。機能は満たしたが、実行が Electron main プロセスの
中で非表示に走るため、次の不満が残った。

- 実行の様子（状態遷移・aider の呼び出し・ツール実行）を人が見られない。
  他の CLI の定常業務は tmux ウィンドウで「CLI が動く様子」ごと見せている。
- 実行境界が dashboard プロセス側にあるため、win32 では aider / ollama が実際に居る
  WSL 側ではなく Windows 側の PATH に依存する。
- 実行器が dashboard 専用で、agent-loop（定期駆動側）から同じ実行を再利用できない。

そこで「tmux を起動して aider CLI を動かすスタイル」へ変える。素の aider は対話
セッションにステートマシン実行文を送っても完遂できない（スキル読込・コマンド実行・
状態遷移を持たない）ため、プロンプトを送る先は素の aider ペインではなく、
**agent-loop に新設するステートマシンハーネス**とする。

## 要件

- 定型業務の Aider 実行が tmux セッションの中で見える（アタッチ・離脱できる）。
- ハーネスは agent-loop 側に置き、dashboard 以外（手動 CLI・定期駆動）からも使える。
- モデルを実行ごとに指定できる（段解決の結果を今回の実行にだけ適用する）。
- 限定ツール契約（`read_files` / `write_files` / `run` / `final`）と検証規則は
  in-process 実行器と同一を保つ。
- 状態遷移は statemachine-use スキルのスクリプト（`next_state.py` / `run_machine.py`）を
  正典として使う。
- ウィンドウを開けない環境（CI 等）でも従来の結果契約（ok / stdout / finalState /
  logFile / files）で完走できる。

## 検討した案

| アプローチ | 実装コスト | リスク | 保守性 | 拡張性 | 推奨度 |
|---|---|---|---|---|---|
| agent-loop に headless ハーネスを移植し tmux ウィンドウで実行 | 中 | 中 | 高 | 高 | ★★★ |
| aider に interactive 定義を追加してプロンプトを send-keys | 低 | 高 | 低 | 低 | ★☆☆ |
| in-process 実行器を残し tmux へログを流すだけ | 低 | 中 | 低 | 低 | ★☆☆ |

素の対話 aider へのプロンプト送信は、スキル読込とコマンド実行が無いままなので
ステートマシンが完遂しない（前設計で確認済みの制約）。ログ転送だけでは実行境界が
dashboard プロセスのままで、win32 の PATH 問題も agent-loop からの再利用も解けない。

## 採用設計

### agent-loop `statemachine` サブコマンド

```
agent-loop statemachine --workflow .statemachine/<name>/workflow.yaml \
    [--agent-cli aider] [--model MODEL] [--param KEY=VALUE ...] [--input TEXT] \
    [--dir DIR] [--hold]
```

- `agent_loop/statemachine.py` に in-process 実行器の限定ツールループを移植する。
  契約・検証規則・プランナープロンプト・ログ形式（`.statemachine-use/logs/*.jsonl`）は
  同一。進行（状態・遷移・ツール実行）は stdout に短く流し、tmux ペインで見える。
- CLI とモデルの解決は agentcore.agentcli（`agents/<name>.json` 契約）へ委譲する。
  `--model` 省略時は定義の `default_model`。`--agent-cli` の既定は aider。
- ワークフロー検証（`run_machine.py --dry-run`）・初期状態・遷移確定（`next_state.py`）は
  statemachine-use スキルを解決して使う（cwd → リポジトリ → `~/.agents/skills` →
  `~/.codex/skills`）。
- 終了時に `RESULT {json}` を 1 行出力する（ok / stdout / finalState / logFile / files）。
  `--hold` は結果を残したままウィンドウを保持する（Enter で閉じる。tmux ウィンドウ実行用）。

### dashboard の実行経路

`runStateMachine` の非対話（Aider）分岐は in-process 実行をやめ、次のとおり起動する。

- ウィンドウ実行（既定）: `runCommandWindow` で実行ごとに一意な tmux セッション
  （`agent-sm-<cli>-<digest>-<ts>`）を作り、`<loopCommand> statemachine … --hold` を
  起動してアタッチする。生存チェック・実行ログ（tracePreamble）は chat ウィンドウと
  同じ流儀。ウィンドウを閉じても実行は tmux 側で続く。
- 非ウィンドウ環境: `runCommandCapture` で非同期に完走させ、`RESULT` 行を解析して
  従来の実行履歴契約のまま記録する（main プロセスは止めない）。
- ワークフローは cwd 相対 POSIX パスで渡す（win32 の dashboard から WSL 側の
  agent-loop を呼んでも同じ場所を指す）。段解決した `{agent_cli, model}` は
  `--agent-cli` / `--model` で今回の実行にだけ明示する。

### 変えないこと

- 段の選択・検証（`resolveTier`）、実行前ダイアログ、パラメータ検証は前設計のまま。
- interactive 対応 CLI（kiro / claude / ollama 等）の既存 tmux 経路は変更しない。
- aider に偽の `interactive` 定義は追加しない（素の対話 aider はステートマシンを
  完遂できないという前設計の判断を維持する）。

## データフロー

```text
今すぐ実行（Aider 段）
  → main で段を再検証・候補解決（従来どおり）
  → agent-loop statemachine --workflow … --agent-cli aider --model <tier解決> --param …
     （tmux セッション agent-sm-… の中で実行・アタッチして見せる）
       → workflow 検証（run_machine.py --dry-run）
       → 状態ごと: プランナープロンプト → aider headless 呼び出し
          → ツール要求検証 → argv 実行 / ファイル割当
       → next_state.py で遷移確定
       → RESULT {ok, stdout, finalState, logFile, files}
  → 実行履歴・ログ・画面状態（従来契約）
```

## エラー処理

- CLI 定義・モデル・ワークフローを解決できない: `RESULT {ok:false, error}` と非 0 終了。
  ウィンドウには原因がそのまま残る（`--hold`）。
- tmux セッションが起動直後に消えた: 同じコマンドをウィンドウ内で直接実行して
  CLI 自身のエラーを見せる（chat ウィンドウと同じ流儀）。
- ツール要求の検証違反・パス逸脱・シェル要求: 実行せず拒否理由を Aider と JSONL ログへ返す。
- 非ウィンドウ実行のタイムアウト: プロセスを止め、タイムアウトを実行履歴に記録する。

## テスト方針

- ハーネス（Python）: パス検証（`..`・シンボリックリンク逸脱）、シェル拒否、
  `.py` の Python 経由実行、スキル相対パスの許可、JSON 応答の抽出（最後の完全な
  オブジェクト）、スタブ aider による「成功申告 → 書込補正 → 検証 → 終端遷移」の完走、
  `--param` / `--input` の解釈。
- dashboard（JS）: ハーネス起動引数（相対ワークフロー・`--model`・`--param`）、
  ウィンドウスクリプト（tmux new-session・生存チェック・アタッチ・`--hold`）、
  非ウィンドウ実行の `RESULT` 行解析と exit code フォールバック。
- 既存の定常業務・agent-loop 全テストの回帰なし。

## Decision Record

| 項目 | 内容 |
|---|---|
| 決定日 | 2026-08-11 |
| 決定者 | ユーザー |
| 採用案 | agent-loop へのステートマシンハーネス移植と tmux ウィンドウ実行（モデル指定つき） |
| 却下案 | aider の偽 interactive 定義＋プロンプト送信（完遂できない）、in-process 実行器の温存（見えない・再利用できない） |
| 主な理由 | 実行の可視化と実行境界の WSL 側への移動を、既存の限定ツール契約と statemachine-use 正典を保ったまま実現できるため |
| トレードオフ | ハーネスの実装が JS から Python へ移る（dashboard の in-process 実行器は削除） |
| 再評価条件 | 2 つ目の headless スキルを追加する、または agent-loop の定期駆動からステートマシンを直接発火したくなった場合 |
