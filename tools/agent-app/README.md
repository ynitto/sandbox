# agent-app

GitHub Copilot App 風のデスクトップ。ローカルリポジトリを登録し、`agents/*.json` に定義した
エージェント CLI（copilot / claude / codex / kiro / cursor …）と会話形式で作業する Electron アプリ。
GitHub との連携は持たない。見に行くのは登録したフォルダだけで、CLI はこの PC（Windows なら WSL）に
入っているものをそのまま呼ぶ。同じリポジトリの**タスク**（statemachine-use スキルで動くステートマシン、
`.statemachine/<名前>/`）と**ワークフロー**（agent-flow の工程）もこの画面で作り、実行する
（旧 statemachine-maker はこのアプリに統合した。単体アプリは無い）。

設計判断は [`docs/designs/agent-app-design.md`](../../docs/designs/agent-app-design.md)、IPC・設定・
保存形式・tmux 契約・上限は [`docs/specs/agent-app-spec.md`](../../docs/specs/agent-app-spec.md) にある。

- **左**: 主メニューは **会話 / タスク / ワークフロー** の 3 つ。選んだ領域の一覧とリポジトリを同じ
  サイドバーで切り替える。会話一覧では応答中・確認待ちに印が付き、
  別の会話を開いて並行して進められる。会話ごとに **git worktree** で作業フォルダを分けられる
  （同じフォルダを複数の CLI が同時に書き換えて混ざるのを避ける）
- **中央「会話」**: チャット。依頼ごとに起動方針（おすすめ / 節約 / 品質重視 / 直接指定）と
  モード（実行 / Ask）を選ぶ。応答は **思考・進捗 / 回答 / 実行情報** に分かれ、回答は通常の
  チャット吹き出し、詳細は折りたたみで表示する。直接指定なら同じ会話の中で
  claude → codex → claude のように渡り歩ける。
  エージェントの選択肢には、agent-herd 一族（aider / ollama）があれば **`herd`** も並ぶ。`herd` は
  ローカル実行系を 1 語で指す仮想の名前で、一族の共通 TUI（agent-herd）を 1 本開き、Ask なら `/find`、
  作業フォルダのファイルを添えた依頼なら `/edit` を本文の先頭に付けて送る（どのエージェントで直すかは
  agent-herd 側の宣言が決める）。タスク・ワークフローでも `herd` を選べ、agent-herd の既定に任せる
  （設定 > 実行制御の tier にも書ける。規則は `src/main/herd.js`）。
  依頼には**ファイルを添付**できる（「添付」ボタン・ドラッグ＆ドロップ・画像の貼り付け・
  「ファイル」画面で開いているファイル）。CLI は **tmux 上で対話起動**され、応答は Markdown として
  描画する。「端末」で CLI の画面をそのまま見て操作できる（ツール実行の許可などは端末で答える）
- **中央「タスク」**: 定義があるタスク（statemachine-use スキルや手で作った `.statemachine/<名前>/` を含む）は
  **実行詳細**（概要 / 手順 / 履歴）から開き、名前の横に「利用可能」が付く。そのまま実行・定期実行できる。
  **新しいタスクは AI との tmux 会話で作る**——目的を書いて「AIと作成を始める」と、会話と同じ CLI が
  リポジトリで起動し、手動実行の画面と同じく端末がタスク画面の中に埋め込まれる。AI は
  `statemachine-use` スキルの作成モードで `.statemachine/<名前>/` を直接書き、画面操作の見本が
  要るときは `@record` の 1 行で頼んでくる（→「タスクを AI と作る」）。既存タスクの変更は「手順」タブの
  「編集」から同じ会話で続ける。定義があるタスクは会話の途中でも実行できる。
  会話の利用者メッセージにある「この依頼をタスクにする」から、依頼の本文を作成フォームへ引き継げる。
  設計: [`docs/plans/2026-09-08-agent-app-statemachine-maker-consolidation-tmux-teaching-design.md`](../../docs/plans/2026-09-08-agent-app-statemachine-maker-consolidation-tmux-teaching-design.md)
- **中央「ファイル」**: リポジトリのフォルダツリーと、コード（言語ごとの配色・行番号）／
  Markdown（プレビュー ⇄ ソース切り替え、Mermaid 図）／画像のビュアー。名前検索はフォルダ全体の
  索引（60 秒保持。`node_modules` や `dist` には潜らない）を引くので、2 文字目からは歩き直さない。
  Windows で `\\wsl$\` のリポジトリなら索引は WSL の中の `git ls-files` で作る（9P 越しに歩かない）。
  ツリーの「更新」で索引も作り直す
- **右「変更」**: その会話の作業フォルダの差分（`git status` / `git diff`）。1 列と並べて表示を
  切り替えられ、ターンが終わるたびに更新する。ダブルクリックでそのファイルをビュアーで開く。
  worktree の会話では「作業ツリー」（まだコミットしていない分）と「ブランチ」（分岐元から
  積んだコミット）を切り替えられる

## 起動

```bash
cd tools/agent-app
npm install        # 画面で使うライブラリを src/renderer/vendor/ へ写す（scripts/vendor.js）
npm start
```

テストはユニットテストに加え、利用できる環境では Electron 実機と疑似 CLI の tmux 統合テストも走る:

```bash
npm test
```

### Windows 向け exe を作る

agent-dashboard と同じく electron-builder で **portable exe** と **NSIS インストーラ** を作る
（設定は `package.json` の `build`）。

```bash
cd tools/agent-app && npm install
npm run dist             # portable + NSIS → release/（release/agent-app.exe が portable 版）
npm run dist:portable    # portable だけ
```

- **同梱するもの**: `src/`（`npm install` が写す `src/renderer/vendor/` と、タスク・ワークフローの共有
  ワークベンチ `src/main/automation/` `src/renderer/automation/` を含む）と、本番依存の `yaml`（定義の
  読み書き）。画面用ライブラリ（mermaid / marked / xterm …）は vendor/ に写した分だけ使うので
  `devDependencies` に置き、electron-builder が本番依存として推移的に同梱しないようにする
  （`dependencies` に戻すと d3 / katex … まで入って asar が数十 MB 増える）。
- `npm run dist` は先に `scripts/check-dist.js` で、本番依存・vendor/・アイコンが揃っているかを見る。
- **リポジトリ直下の資源**は `extraResources` で `resources/` に入れる。CLI 定義 `agents/*.json` は
  `resources/agents/`（探索順の最後。`~/.agents/agents/` などに置いた定義が勝つ）、タスク実行に要る
  `.github/skills/statemachine-use` は `resources/app-root/.github/skills/…`（登録リポジトリや
  ⚙ 設定で見つからないときの最後の候補）。参照と同梱指定の対応は `test/packaging.test.js` が突き合わせる。
- **アイコン**は `assets/icon.ico`（`npm run icon` → `scripts/icon.js` が外部ライブラリなしで生成。
  吹き出しに `›_` の図柄で、agent-dashboard とは別物）。差し替えるなら 256px を含む ico を同じ場所に置く。
- Linux 上でも `npm run dist` は通る（electron-builder 26 は wine なしで exe のアイコン・バージョン情報を
  書き換え、NSIS も同梱の物を使う）。署名は行わない。

### 前提

| | Linux / macOS | Windows |
|---|---|---|
| CLI（claude / kiro …） | この OS の PATH（ログインシェル） | **WSL の中**の PATH |
| tmux | `apt install tmux` など | WSL の中に `sudo apt install tmux` |
| git（変更ビュー） | ローカル | WSL の中 |
| Python 3 + PyYAML（タスクの構成確認・agent-loop 無しの実行） | ローカル | 構成確認はこの端末、実行は WSL の中の `python3` |
| agent-tools（任意） | agent-herd / agent-loop / agent-flow を PATH に | WSL の中の PATH に |
| 登録するフォルダ | そのまま | `\\wsl$\<ディストロ>\…` か `C:\…`。tmux の cwd と git には WSL 表記（`/home/…` / `/mnt/c/…`）へ直して渡す |

Windows では `\\wsl$\Ubuntu\…` のリポジトリはパスからディストロが決まる。`C:\…` のリポジトリは
左下の「WSL ディストロ」で指定したもの（空なら既定のディストロ）で動かす。

tmux が無い（または「設定 > アプリ」で対話セッション維持を外した）ときは、従来どおり 1 ターン 1 プロセスの
ヘッドレス実行（`-p` 相当）に倒れる。

### agent-tools が無くても動く / あると増えるもの

agent-app は **agent-tools（agent-herd / agent-loop / agent-flow）を入れていない PC でも、会話とタスクの
作成・実行が動く。** 要るのは CLI（claude / codex / copilot / kiro …）と tmux、タスクの実行に Python 3 と
PyYAML（`pip install pyyaml`。Windows では WSL の中）だけ。定義 `agents/*.json` は agent-app 自身が読み、
「使える」印もホストの PATH で付けるので、会話で使える CLI がそのままタスクでも使える。

| | agent-tools なし | agent-herd（ローカル実行系） | agent-loop | agent-flow |
|---|---|---|---|---|
| 会話 | ○ | ＋ `herd`（費用 0 のローカル LLM。Ask は読み取り専用が保証される） | — | — |
| タスクを AI と作る | ○ | ＋ `herd` を会話の既定にできる | — | — |
| タスクの手動実行 | ○（同梱の statemachine-use スキルが、定義から組んだ CLI を工程ごとに起こす） | ＋ タスクの AI に `herd` を選べる | 実行の正典がこちらに移る（実行ログ・台帳・受入条件・`check` の昇格） | — |
| 定期実行・実行履歴 | ×（1 行でそう出る） | — | ○ | — |
| AI 支援（ワークフロー教示・工程の見直し） | ○（その CLI を読み取り専用の単発で起こす） | ＋ `herd` なら agent-herd の計画用途（JSON を文法で強制。修正の往復が減る） | — | — |
| ワークフロー（複数 AI の工程） | × | — | — | ○ |

足りないものは「タスク」画面の実行環境（手順 → その他 → 実行環境）に出る。任意の道具は「任意」と出て、
未準備でも本体は止めない。何が増えるかの設計は
[`docs/plans/2026-09-09-agent-app-standalone-and-herd-benefits-design.md`](../../docs/plans/2026-09-09-agent-app-standalone-and-herd-benefits-design.md)。

## 作業フォルダを分ける（git worktree）

同じリポジトリで会話を並行して進めると、1 つの作業ツリーを複数の CLI が同時に書き換えて混ざる。
会話ごとに worktree を生やせば、**別のフォルダ・別のブランチ**で作業でき、本体（登録したフォルダ）は
そのまま残る。

- 置き場は `<リポジトリ>/.worktrees/<名前>` の 1 か所に決め打つ。名前だけを保存すればよく、
  画面から生のパスを受け取らない（`..` を持ち込めない）。Windows でも「登録したパス + `.worktrees` +
  名前」で fs 用（`C:\…`）と git 用（`/mnt/c/…`）の両方を作れる
- 初回に `.worktrees/` を **`.git/info/exclude`**（ローカル限定の除外）へ足す。リポジトリの
  `.gitignore` は触らない。これをしないと本体の変更ビューに worktree が「新規」として並ぶ
- 左下の「作業フォルダを分ける」で機能ごと on / off できる（既定は on）。off にすると画面から
  選択と `…` が消え、新しい会話は常にリポジトリ本体で始まる。既にある worktree と、それで
  始めた会話はそのまま動く（どこで動いているかは会話を開けば見える）
- 上の「作業フォルダ」で会話ごとに選ぶ。**会話を作ったあとは変えられない**（tmux の cwd も
  CLI 側の文脈もそこで始まっているため）。`…` ボタンで作成・削除
- ブランチ名を入れるとフォルダ名は自動で決まる（`feature/foo` → `.worktrees/feature-foo`）。
  既にあるブランチを指定すると、新しく作らずそれを持ってくる
- 削除は未コミットの変更が残っていると git が断る（確認のうえ「変更ごと削除」で押し切れる）。
  ブランチは既定で残す。その作業フォルダを使っている会話の tmux セッションは先に止める
- この画面の外で作った worktree は一覧には出すが、会話の作業フォルダには選べない
- `.worktrees` 自体は本体のツリーと名前検索から外す（リポジトリの写しなので、出すと入れ子の
  複製が並び、検索も worktree の数だけ同じファイルを返す）。中を見るときは「見るフォルダ」で選ぶ

git へ書き込むのはここだけ（worktree の追加・削除とブランチ作成）。変更ビューは読むだけで、
コミット・マージ・push は持たない——それは CLI に頼むか、端末でやる。

## 起動方針とターンごとの実行設定

「設定 > 実行制御」で small / medium / large の各 tier にエージェントとモデルを割り当てる。
おすすめは medium、節約は small、品質重視は large を決定的に選び、利用不能でも別 tier へは
自動フォールバックしない。「直接指定」では次のターンだけエージェントとモデルを選べる。
各依頼には実際に使った起動条件が残る。

**エージェントを最適化する**（設定 > 実行制御。既定 ON）が効いているときだけ、節約・品質重視と small / large
tier が使える。効くのは agent-herd（ローカル実行系）が使えるときで、OFF にするか agent-herd が無ければ、
既定の起動方針は「おすすめ」だけ、ターンごとの起動方針は「おすすめ」か「直接指定」だけになり、選べない項目と
small / large の行は薄くなる（理由は出さない）。保存してある節約・品質重視は、その間は おすすめ として扱う。
同様に、agent-flow が無ければサイドバーの「ワークフロー」、agent-loop が無ければタスクの「履歴」タブと
「定期実行」のカードが薄くなる。

- **ヘッドレス**（1 ターン 1 プロセス）は、そのターンの CLI を選んだモデル・モードで起動するだけ
- **tmux**（対話起動）は、動いている CLI と起動条件が違えば、そのターンの前に **CLI を起動し直す**
  （以前の「再起動」ボタンを押す手間が自動になった）。同じ CLI なら `--resume <ID>`（claude / copilot）や
  `codex resume --last` で文脈を引き継ぐ。再開手段の無い CLI（kiro / cursor …）は、これまでの
  やり取りを最初の依頼に添えて起動する
- **エージェントを渡り歩く**と、会話は CLI ごとに別のセッションになる。会話は CLI ごとに
  「セッション ID」と「そこまで見たメッセージ数」を覚えていて、別の CLI で進めた分は、戻ってきた
  ときに依頼の前に「あなたのセッションの外で進んだやり取り」として添える（履歴の再送は
  その差分だけで、全部を毎回送り直しはしない）
- tmux の会話は 1 つの tmux セッション（＝ CLI 1 つ）しか持たない。別の CLI へ移ると前の CLI は
  止める（戻るときは resume で続く）。対話定義を持たない CLI（vscode-copilot 等）へ移ったターンは
  ヘッドレスで走る
- 作業フォルダ（worktree）だけは会話ごとに固定のまま（tmux の cwd も CLI 側の文脈もそこで始まっている）

## 共通指示と起動時アクション

「設定 > 共通指示」では、すべての依頼へ加える短い指示、推奨スキル、起動時アクションを設定できる。
生の JSON 編集は行わず、画面のコントロールから `config.json` へ保存する。

- 共通指示はリポジトリ固有・依頼固有の指示を優先する旨とともに各ターンへ付ける
- 推奨スキルは利用可能なスキル／コマンドから選べ、任意の名前も入力できる
- 起動時アクションはスキルまたはシェルコマンド。CLI ごとの新しいセッションで上から一度だけ適用する
- コマンドは作業フォルダで実行し、1 件 60 秒・全体 120 秒。失敗時は「続行」または「停止」を選べる
- 同時実行数は 1〜8。上限時は待ち行列を作らず、その場で再実行を案内する

## ファイルを添付する

依頼にファイルを付けると、CLI には依頼文の末尾に「添付ファイル: <パス>」として伝わり、CLI 自身の
ファイル読み取りツールで読ませる（画像も同じ。claude / codex / copilot / kiro は画像ファイルを読める）。
`file_flag` を宣言する定義（aider 等）には argv でも渡す。

- **外のファイル**（「添付」ボタン・ドロップ・貼り付け）は userData の `attachments/<ID>/<名前>` へ
  写し、そのパスを伝える。Windows では WSL 表記（`/mnt/c/…`）へ直す。画面は生のパスを持たず、
  以後の参照は ID だけ。上限は 1 つ 25 MB・1 ターン 20 個
- **リポジトリの中のファイル**（「ファイル」画面の「会話に添付」）は写さず、相対パスを伝えるだけ。
  会話の作業フォルダの中にあるものだけ選べる
- 依頼の吹き出しに添付の印が付く。写したものはクリックで既定のアプリ、リポジトリの中のものは
  ビュアーで開く。会話を削除すると写した添付も消える

## 使っている外部ライブラリ

| 用途 | ライブラリ |
|---|---|
| 端末ミラー | @xterm/xterm, @xterm/addon-fit |
| コードの配色 | highlight.js（`@highlightjs/cdn-assets`。同梱セット + Dockerfile / PowerShell などを追加） |
| Markdown | marked + DOMPurify（無害化） |
| 図 | mermaid |
| 差分 | diff2html |

配布物は `npm install` 時に `scripts/vendor.js` が `src/renderer/vendor/` へ写す（CSP は
`script-src 'self'` のまま。CDN は使わない）。写した後は node_modules を参照しないので、これらは
`devDependencies` に置く（exe に同梱しない。→「Windows 向け exe を作る」）。

## CLI の呼び方（tmux）

定義の `interactive` 節（正典は `schemas/agent-cli.schema.json`）で対話起動する。
`interactive` 節を持たない定義は自動的にヘッドレスになる。

```
interactive.command + [continue | resume] + (interactive.write_args | readonly_args) + model_flag model
```

- 会話 1 つ = tmux セッション 1 つ（`agent-app-<会話 ID の先頭 12 桁>`）。tmux サーバは自前の
  ソケット `-L agent-app` に持ち、利用者の tmux とは干渉しない。人が覗くときは
  `tmux -L agent-app attach -t <名前>`（画面の端末ドロワーにも出る）
- 依頼は `send-keys -l`（1 行）か `set-buffer` + `paste-buffer -p`（複数行）で流し込み、少し置いて Enter
- ターンの終わりは定義の `ready_pattern`（末尾 `ready_tail_lines` 行）/ `busy_pattern`（画面全体）/
  `idle_quiet_sec` で判定する。y/n や許可を求めていそうな画面は「確認待ち」として知らせる
- 応答本文は送信前後のスクロールバック（`capture-pane -J -S -`）の差分から、入力欄・枠線・
  フッター・依頼の echo を除いて拾う。読み取れなかったときは端末を見る
- 画面は `capture-pane -e` を 0.25〜1.2 秒ごとに写して xterm に描く（node-pty も attach も使わない）。
  Windows では `wsl.exe -e bash -l` を 1 本常駐させてそこへ流すので、1 回ごとに wsl.exe を起こさない
- 停止は `busy_pattern` に esc が出てくる CLI（claude / codex）には Escape、それ以外は C-c
- CLI の終了コードは、ペインの最後の行として自分で印字したものを読む。tmux は pty が閉じた時点で
  ペインを終了扱いにするが、終了コードはそのまま出てこないことがある（3.4 で実測）
- アプリを閉じても tmux セッションは残り、次に会話を開いたときに再接続する。CLI が終了していたら
  「再起動」か次の依頼で作り直す（エージェント・モデル・モードを変えたターンも作り直す）。
  再開の作法は CLI ごとに違う:

| CLI | 起動 | 作り直すとき |
|---|---|---|
| claude / copilot | こちらで UUID を発行して `--session-id` | `--resume <UUID>` |
| codex | 何も足さない | `codex resume --last` |
| kiro / cursor / ollama | 何も足さない | 再開手段なし（これまでのやり取りを最初の依頼に添える） |

ヘッドレス（tmux なし）の作法は以前のまま（`src/main/agentCli.js` の `SESSION` 表）。Windows では
`wsl.exe -e bash -lc` に載せて WSL の中で走らせる。

## タスクを AI と作る

タスクの作成と変更は、会話と同じ **tmux の端末ミラー**の中で進める。CLI（会話の既定の起動方針で決まる
エージェント）がリポジトリ本体を cwd に起動し、最初の依頼で `statemachine-use` スキルの作成モードと、
保存先 `.statemachine/<名前>/`、見本の頼み方を伝える。AI はこの会話の中で定義（workflow.yaml と
actions/*.md）を直接書き、`run_machine.py --dry-run` で検証してから要約を報告する。定義ができた時点で
「利用可能」になり、確認は「概要」の実行と構成確認で行う（分離した試運転や承認の往復は持たない）。

| したいこと | 操作 |
|---|---|
| 新しいタスクを作る | 「タスク」の ＋ → 目的を書く →「AIと作成を始める」。保存名は空なら目的から決まる |
| AI の質問に答える | 端末の下の入力欄から送る（メッセージ。Shift+Enter で改行）。y/n や矢印キーは「端末操作」に切り替える。端末操作で送信せずに行を足すのは「改行」キー（Shift+Enter / Ctrl+J） |
| ツール実行の確認を省く | 作成設定・編集設定の「権限」を「自動承認」にする（`powershell.exe` の実行なども確認なしで通る）。会話を開いた後に変えても次の依頼から効く。初期値は設定の「会話の既定権限」 |
| 操作を見せる | 「操作の見本」→ 画面（ブラウザ / Windows アプリ）と URL（アプリ名）→ あとは**同じボタンを押していく**。ブラウザは「ブラウザを開く」→（開いた Edge でログインや画面の移動）→「記録を始める」→ 見せたい操作 →「終了してAIへ渡す」の 3 回。Windows アプリは「記録を始める」→ 操作 →「終了してAIへ渡す」の 2 回。押した段だけが AI に伝わるので、**準備の操作は見本に入らない**。取り直すときは「やり直す」。AI が `@record browser <URL>` / `@record windows <アプリ名>` と返したときは、そのカードが自動で開く |
| 既存のタスクを変える | 実行詳細の「手順」→「編集」。押した時点で AI が起動し、今の定義を読んで要約してから、変えたい点を聞く。「‹ 工程に戻る」で工程へ戻る |
| 会話をやり直す | 端末が終了・消失したら「再接続」。会話は `sessions/` に残り、次に開いたときに tmux へつなぎ直す |
| 内部の工程を確認する | 「手順」タブ。従来の工程エディタで、そのまま直して保存もできる |

**見本の取り方は画面の種類で違う。** Windows では AI は WSL の tmux で動いていて、ブラウザや Windows アプリは
Windows 側にある。WSL から Windows 側の `playwright-cli` を起こすことはできないので、次の 2 通りに分ける。

| 画面 | 記録するのは | 流れ |
|---|---|---|
| ブラウザ | **AI 自身**（WSL 側の `playwright-cli`） | ボタン 1 つを 3 回押す。①「ブラウザを開く」で agent-app が Windows 側の Edge をリモートデバッグ付き（`http://localhost:9222`、記録専用のプロファイル）で起こし、そのことを**固定文**（`@recording open` で始まり、接続先を含む）として tmux 経由で AI へ渡す。AI は `playwright-cli attach --cdp=…` で接続だけして待つ——ここで利用者がログインや目的の画面までの移動をする。②「記録を始める」で固定文（`@recording start`。いま開いているページを含む）が渡り、AI が `recording-start` で記録を始めて利用者の操作を待つ。③「終了してAIへ渡す」で固定文（`@recording stop`）が渡り、AI が `recording-stop` で止めて記録の行を `.statemachine/<名前>/recordings/<時刻>-browser.md` に保存し、それを根拠に工程を組む。途中で「やり直す」を押すと固定文（`@recording cancel`）が渡り、AI は取りかけの記録を保存せずに捨てる |
| Windows アプリ | **agent-app**（Windows 側の `winauto`） | 「記録を始める」で agent-app 自身が Windows 側で記録を起こし、「終了してAIへ渡す」でできた Markdown（`.statemachine/<名前>/recordings/<時刻>-windows.md`。操作の行と毎回変わる値の候補）の所在を WSL 表記（`/mnt/c/…`）へ直して会話へ送る。AI 自身には記録を起こさせない（依頼文でそう伝える） |

ブラウザの見本の流れはすべて依頼文に仕込んであり、AI は固定文が届く前にブラウザを起こしたり記録を始めたり
しない。ボタンを押す番になると AI がそう言う（そのとき見本のカードは自動で開く）。**ログイン・画面の移動は
①と②の間に済ませる**——②を押してからの操作だけが見本になるので、毎回変わらない準備が工程に混ざらない。
Linux / macOS でも同じ流れで、Edge（無ければ Chrome）はこの端末で開く。

### 生成する定義の形

statemachine-use の作成モードの原則に沿う（`SKILL.md` ステップ 2）:

- 1 ステート 1 工程。`action_file: actions/<id>.md`。本文の末尾は単一指示。
- 出力契約は `output_validator: startswith:<ラベル…>`（既定は `OK,FAILED`）。
- 分岐は `condition_rule`（`startswith:last_output:<ラベル>`）。`check` を宣言した工程は `equals:check_ok:true` で進む。
- 画面操作は `playwright-cli` / `windows-app-automation` スキルを本文で名指しする。記録した操作は role と名前で載せる。
- 終端は `complete`（完了）と `failed`（失敗）。どこからも行かない終端は書かない。
- `maker.json` は「手順」タブの編集画面が読み戻すための写しで、実行には使わない（AI が書かなくてもよい）。

### 次の工程（「手順」タブ）

工程ごとに、行き先を上から順に並べる。決め方は 4 つ。

| 決め方 | 何を見るか | 書かれるもの |
|---|---|---|
| 回答が指定の言葉で始まる | 出力の 1 行目がその語で始まるか | `condition_rule: startswith:last_output:<語>` |
| 条件に当てはまる | その文にあてはまるか（AI が見る） | `condition: <文章>` |
| 常に | 条件なし | 条件を付けない |
| 詳細条件 | 読み込んだ式をそのまま | `condition_rule: <式>` |

何も足さなければ「できた → 次へ」「できなかった → 中止」になる。行き先は次の工程・完了・中止のほか、
**定義が持つ終わり方**（承認 / 差し戻し / 判別できない…）や、前の工程へ戻ることも選べる。
第 1 行の出力契約を書くのは、行き先が**すべて**回答の先頭で決まるときだけ。

### 記録がうまくいかないとき

ブラウザの見本は、この端末が Edge を起こし、**AI（Windows では WSL）側の `playwright-cli`** が接続して記録する。
Windows アプリの見本は `winauto` を**この端末から直接**呼ぶ（WSL には橋渡ししない）。「手順」→「その他 →
実行環境 → 接続を確認」でこの端末側の道具が呼べるかを先に確かめる。

| 症状 | 見るところ |
|---|---|
| 「Edge か Chrome が見つかりません」と出る | Microsoft Edge を入れる。Windows は既定の置き場（Program Files / ユーザーの AppData）、Linux / macOS は PATH か Applications で探す |
| Edge は開くが「応答しません」と出る | ポート 9222 を別のブラウザやツールが使っている。そのブラウザを閉じてからもう一度「ブラウザを開く」 |
| Edge がいつもと違うプロファイル（ログインしていない）で開く | 記録専用のプロファイルで開く仕様（近年の Edge / Chrome は既定のプロファイルでリモートデバッグを受け付けない）。1 回ログインすれば次回からは残る |
| AI が「接続先に届かない」と言う | WSL のネットワークが既定（NAT）だと、WSL の `localhost` は Windows 側に届かない。`%UserProfile%\.wslconfig` に `[wsl2]` `networkingMode=mirrored` を書いて `wsl --shutdown` する。AI 側に `playwright-cli` が無いときは WSL の中で `npm install -g @playwright/cli@latest` |
| 操作したのに記録が空になる | 記録するのは**アプリが開いた Edge の窓**の中の操作だけ。別に開いていたブラウザで操作しても入らない |
| Windows アプリの見本が取れない | `winauto` は Windows 上でだけ動く（`python tools/winauto/install.py`）。Linux / macOS では見本の画面にそう表示する |
| AI が固定文の前にブラウザを起こそうとする | 依頼文で「固定文が届く前に起こさない」と伝えている。それでも起こしたら、端末操作で止めて（Ctrl+C）「ブラウザを開く」から押し直す |
| 準備の操作まで工程に入ってしまった | ①「ブラウザを開く」の後に②「記録を始める」を押さずに操作している。「やり直す」で取りかけの記録を捨てて、①からやり直す |

agent-app の「手順」タブでは、旧来の「その他 → 操作を記録」は表示しない。ブラウザや Windows アプリの操作を見せるときは
「AIと編集」で tmux を開き、入力欄の「操作の見本」を使う。AI が返答に `@record browser <開始 URL>` または
`@record windows <アプリ名>` を単独行で返した場合も、同じ記録欄が自動で開く。

### 画面の言葉

内部の綴りをそのまま出さない。画面には次の言葉を使う（`test/automation-app.test.js` が検査する）。

| 内部 | 画面 |
|---|---|
| ステートの ID | 工程ID（「詳細設定」の中） |
| 識別名・フォルダ名 | 保存名 |
| `output_validator` の第 1 行 / 出力契約 | 回答が指定の言葉で始まる |
| `check` / 終了コード 0 | 完了確認／確認できたら |
| `check_retries` | 再試行回数 |
| transitions / 遷移 | 次の工程 |
| 既定の OK / FAILED | できた／できなかった |
| `{{key}}` / 入力パラメータ | 毎回変わる値 |
| `--dry-run` | 構成を確認 |
| `--agent-cli`（agent-tools の定義名） | 使う AI |
| 終端ステート | 終わり方（いくつあっても行き先に選べる） |

### 画面を直すときの落とし穴

renderer で **`const api = …` のように preload が公開した名前を宣言してはいけない**。
`contextBridge` が置く `window.api` は再定義できないので、宣言するとスクリプトの実行前に
`Identifier 'api' has already been declared` で落ち、**画面が真っ白**になる。共有ワークベンチ
（`src/renderer/automation/`）は Shadow DOM の中で動くが、`window.api.automation` を読むだけにする。

## 保存先

Electron の userData（macOS は `~/Library/Application Support/agent-app`）にだけ書く。

```
config.json          登録リポジトリ、アプリ設定、共通指示・推奨スキル・起動時アクション、実行方針・tier・同時実行数
sessions/<id>.json   会話 1 つ。リポジトリ・次のターンの方針 / CLI / モデル / モード・transport（tmux | headless）・作業フォルダ・
                     メッセージ列（各依頼の起動条件・添付、応答の思考 / 回答 / 実行情報）・CLI ごとのセッション ID と見たメッセージ数・
                     tmux で動いている CLI の起動条件。タスクを AI と作る会話は kind: task で保存名（task.machine）に紐づき、
                     会話一覧には出ない
attachments/<id>/    依頼に添えた外のファイルの写し
```

リポジトリ側に置くのは、作業フォルダを使うときの `.worktrees/<名前>`（git の管轄）と
`.git/info/exclude` の 1 行、タスクの定義 `.statemachine/<名前>/`（AI との会話で書く。下書きの印
`teaching.json` と見本の記録 `recordings/` を含む）、ワークフローの定義 `.agents/workflows/`。
CLI 自身のセッションログ（`~/.claude/projects` など）は CLI の管轄。

## 持たないもの

- GitHub 連携（PR・Issue・クラウドセッション）
- ツール呼び出しの逐次承認 UI。CLI が端末で聞いてきたら端末ドロワーで答える（タスクを作る会話も同じ）
- タスクの試運転・承認の往復（AI の候補を JSON で受けて試運転してから昇格する仕組み）。定義は会話の中で
  書き、確認は実行と構成確認で行う
- 差分の適用・取り消し、コミット・マージ・push。変更ビューは読むだけ（git へ書くのは
  worktree の追加・削除とブランチ作成だけ）
- ファイルの編集。ビュアーは読むだけ（「開く」で既定のアプリへ）
