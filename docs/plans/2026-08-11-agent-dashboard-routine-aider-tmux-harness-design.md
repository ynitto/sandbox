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
    [--agent-cli aider] [--model MODEL] [--param KEY=VALUE ...] [--input TEXT] [--dir DIR]
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
  dashboard はこの行を実行結果の契約として読む。

### dashboard の実行経路

`runStateMachine` の非対話（Aider）分岐は in-process 実行をやめ、次のとおり起動する。

- ウィンドウ実行（既定）: `runCommandWindow` で実行ごとに一意な tmux セッション
  （`agent-sm-<cli>-<digest>-<ts>`）を作り、`<loopCommand> statemachine …` を起動して
  アタッチする。ウィンドウを閉じても実行は tmux 側で続く。
- 非ウィンドウ環境: `runCommandCapture` で非同期に完走させ、`RESULT` 行を解析して
  従来の実行履歴契約のまま記録する（main プロセスは止めない）。
- 段解決した `{agent_cli, model}` は `--agent-cli` / `--model` で今回の実行にだけ明示する。

### Windows → WSL の境界（この設計の要）

実行主体が WSL 側へ移るため、**ビュアー（Windows）のパスと WSL のパスを取り違えると
すべて壊れる**。既存の `sh()` / `chatWindowScript` と同じ規則へ揃える。

- 起動は必ず `wsl.exe` 経由にする。同期・非同期の起動仕様を `cliSpawnSpec` に一本化し、
  win32 では `wsl.exe [-d <distro>] -e sh -lc 'cd <linuxCwd> && …'` を組む。
  Windows 側で直接 `spawn` すると agent-loop は ENOENT になり、Windows に存在しない
  パスを `spawn` の `cwd` へ渡すと起動自体が失敗する。
- tmux の `-c` と `cd` には `toWslCwd()` で翻訳した Linux パスだけを渡す。
- `--workflow` は **実行 cwd からの相対 POSIX パス**で渡す。ビュアーの絶対パスは
  WSL 側で解決できず、相対にすればディストロ表記にも依存しない。作業フォルダの外を
  指す組み合わせ（登録フォルダと定義フォルダが別ボリューム。win32 の `path.relative`
  は道筋が無いと絶対パスを返す）は dashboard 側で起動前に断る。

### tmux セッションの扱い（チャット経路との違い）

一回限りの実行なので、常駐セッション向けの流儀をそのまま使えない。以下は実機の tmux で
挙動を確かめた上での判断。

- **失敗しても同じコマンドを窓で再実行しない**。`chatWindowScript` は「セッションが
  起動直後に消えていたら同じ CLI を窓で直接実行して原因を見せる」が、それは何度
  起動してもよい対話 CLI だから成り立つ。ステートマシンを再実行するとファイル編集ごと
  二重に走る。代わりに、起動できない唯一の実質的な原因（PATH に無い）を
  `command -v` で**事前に**確かめる（agent-loop の `cmd_send` が `which` で確かめて
  いるのと同じ）。tmux は exec に失敗しても何も残さずペインごと消えるため、
  事前に断らないと「一瞬で終わって理由が分からない」だけになる。
- **実行の記録はペインではなくファイルに採る**。当初 `remain-on-exit` でペインを
  残す方式にしたが、実行が終わったあとに人がアタッチすると**そのときのリサイズで
  ペインの内容が消える**（80x24 → 80x23 で確認）。短時間で終わる実行ほど「見に行ったら
  何も無い」になる。`pipe-pane` で出力をファイルへ写し、アタッチから戻ったあとに窓へ
  末尾を出す（`runInWindow` が `tee` で出力を拾っているのと同じ考え方）。
  pipe-pane は placeholder（`sleep`）のうちに繋ぎ、`respawn-pane` で本命へ差し替える
  ——本命をいきなり起動すると、繋ぐ前に出た行を取りこぼす。
- アタッチから戻ったとき、セッションが残っていれば実行中なので再接続方法
  （`tmux attach -t …`）を表示し、消えていれば終了なので記録の末尾を出して片付ける。
  セッションはコマンドの終了とともに自然に消えるので、溜まり続けることはない。

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
  窓には `pipe-pane` で拾った出力の末尾が出る。
- ワークフローが作業フォルダの外を指す: 起動せず、dashboard 側で理由を返す。
- agent-loop が WSL 側に無い: セッションを作る前に `command -v` で断り、窓に理由を残す。
  **再実行はしない**（二重実行を作らない）。
- ツール要求の検証違反・パス逸脱・シェル要求: 実行せず拒否理由を Aider と JSONL ログへ返す。
- 非ウィンドウ実行のタイムアウト: プロセスを止め、タイムアウトを実行履歴に記録する。

## テスト方針

- ハーネス（Python）: パス検証（`..`・シンボリックリンク逸脱）、シェル拒否、
  `.py` の Python 経由実行、スキル相対パスの許可、JSON 応答の抽出（最後の完全な
  オブジェクト）、スタブ aider による「成功申告 → 書込補正 → 検証 → 終端遷移」の完走、
  `--param` / `--input` の解釈。
- dashboard（JS）: ハーネス起動引数（相対ワークフロー・`--model`・`--param`）、
  作業フォルダ外の定義を起動前に断ること、**win32 で `wsl.exe` を経由し WSL パスへ
  翻訳すること**（非ウィンドウ実行・ウィンドウ実行の両方）、ウィンドウスクリプトが
  同じコマンドを二度書かないこと、PATH 事前確認、pipe-pane を本命起動前に繋ぐこと、
  非ウィンドウ実行の `RESULT` 行解析と exit code フォールバック。
- 生成した tmux スクリプトを実物の tmux で走らせ、(1) 実行が終わってから見に行っても
  窓に出力が出る、(2) 実行中に離脱してもセッションが残り再接続方法が出る、
  (3) PATH に無いコマンドは起動前に断る、(4) セッションが残らない、を確認する。
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
