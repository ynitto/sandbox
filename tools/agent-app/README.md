# agent-app

GitHub Copilot App 風のデスクトップ。ローカルリポジトリを登録し、`agents/*.json` に定義した
エージェント CLI（copilot / claude / codex / kiro / cursor …）と会話形式で作業する Electron アプリ。
GitHub との連携は持たない。見に行くのは登録したフォルダだけで、CLI はこの PC（Windows なら WSL）に
入っているものをそのまま呼ぶ。

- **左**: 登録したリポジトリと、そのリポジトリの会話一覧。応答中・確認待ちの会話には印が付き、
  別の会話を開いて並行して進められる。会話ごとに **git worktree** で作業フォルダを分けられる
  （同じフォルダを複数の CLI が同時に書き換えて混ざるのを避ける）
- **中央「会話」**: チャット。上でエージェント・モデル・モード（Agent / Ask）を選び、下に依頼を書く。
  CLI は **tmux 上で対話起動**され、応答は Markdown として描画する。「端末」で CLI の画面を
  そのまま見て操作できる（ツール実行の許可などは端末で答える）
- **中央「ファイル」**: リポジトリのフォルダツリーと、コード（言語ごとの配色・行番号）／
  Markdown（プレビュー ⇄ ソース切り替え、Mermaid 図）／画像のビュアー
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

テストは Electron を起動せずに通る（tmux がある環境では疑似 CLI との統合テストも走る）:

```bash
npm test
```

### 前提

| | Linux / macOS | Windows |
|---|---|---|
| CLI（claude / kiro …） | この OS の PATH（ログインシェル） | **WSL の中**の PATH |
| tmux | `apt install tmux` など | WSL の中に `sudo apt install tmux` |
| git（変更ビュー） | ローカル | WSL の中 |
| 登録するフォルダ | そのまま | `\\wsl$\<ディストロ>\…` か `C:\…`。tmux の cwd と git には WSL 表記（`/home/…` / `/mnt/c/…`）へ直して渡す |

Windows では `\\wsl$\Ubuntu\…` のリポジトリはパスからディストロが決まる。`C:\…` のリポジトリは
左下の「WSL ディストロ」で指定したもの（空なら既定のディストロ）で動かす。

tmux が無い（または左下の「tmux で対話起動」を外した）ときは、従来どおり 1 ターン 1 プロセスの
ヘッドレス実行（`-p` 相当）に倒れる。

## 作業フォルダを分ける（git worktree）

同じリポジトリで会話を並行して進めると、1 つの作業ツリーを複数の CLI が同時に書き換えて混ざる。
会話ごとに worktree を生やせば、**別のフォルダ・別のブランチ**で作業でき、本体（登録したフォルダ）は
そのまま残る。

- 置き場は `<リポジトリ>/.worktrees/<名前>` の 1 か所に決め打つ。名前だけを保存すればよく、
  画面から生のパスを受け取らない（`..` を持ち込めない）。Windows でも「登録したパス + `.worktrees` +
  名前」で fs 用（`C:\…`）と git 用（`/mnt/c/…`）の両方を作れる
- 初回に `.worktrees/` を **`.git/info/exclude`**（ローカル限定の除外）へ足す。リポジトリの
  `.gitignore` は触らない。これをしないと本体の変更ビューに worktree が「新規」として並ぶ
- 上の「作業フォルダ」で会話ごとに選ぶ。**会話を作ったあとは変えられない**（tmux の cwd も
  CLI 側の文脈もそこで始まっているため）。`…` ボタンで作成・削除
- ブランチ名を入れるとフォルダ名は自動で決まる（`feature/foo` → `.worktrees/feature-foo`）。
  既にあるブランチを指定すると、新しく作らずそれを持ってくる
- 削除は未コミットの変更が残っていると git が断る（確認のうえ「変更ごと削除」で押し切れる）。
  ブランチは既定で残す。その作業フォルダを使っている会話の tmux セッションは先に止める
- この画面の外で作った worktree は一覧には出すが、会話の作業フォルダには選べない

git へ書き込むのはここだけ（worktree の追加・削除とブランチ作成）。変更ビューは読むだけで、
コミット・マージ・push は持たない——それは CLI に頼むか、端末でやる。

## 使っている外部ライブラリ

| 用途 | ライブラリ |
|---|---|
| 端末ミラー | @xterm/xterm, @xterm/addon-fit |
| コードの配色 | highlight.js（`@highlightjs/cdn-assets`。同梱セット + Dockerfile / PowerShell などを追加） |
| Markdown | marked + DOMPurify（無害化） |
| 図 | mermaid |
| 差分 | diff2html |

配布物は `npm install` 時に `scripts/vendor.js` が `src/renderer/vendor/` へ写す（CSP は
`script-src 'self'` のまま。CDN は使わない）。

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
- アプリを閉じても tmux セッションは残り、次に会話を開いたときに再接続する。CLI が終了していたら
  「再起動」で作り直す。再開の作法は CLI ごとに違う:

| CLI | 起動 | 作り直すとき |
|---|---|---|
| claude / copilot | こちらで UUID を発行して `--session-id` | `--resume <UUID>` |
| codex | 何も足さない | `codex resume --last` |
| kiro / cursor / ollama | 何も足さない | 再開手段なし（画面の履歴は残る） |

ヘッドレス（tmux なし）の作法は以前のまま（`src/main/agentCli.js` の `SESSION` 表）。Windows では
`wsl.exe -e bash -lc` に載せて WSL の中で走らせる。

## 保存先

Electron の userData（macOS は `~/Library/Application Support/agent-app`）にだけ書く。

```
config.json          登録したリポジトリ、最後に選んだリポジトリ・エージェント・モデル・モード、WSL ディストロ、tmux の要否
sessions/<id>.json   会話 1 つ。リポジトリ・CLI・transport（tmux | headless）・作業フォルダ・メッセージ列・CLI 側のセッション ID
```

リポジトリ側に置くのは、作業フォルダを使うときの `.worktrees/<名前>`（git の管轄）と
`.git/info/exclude` の 1 行だけ。CLI 自身のセッションログ（`~/.claude/projects` など）は CLI の管轄。

## 持たないもの

- GitHub 連携（PR・Issue・クラウドセッション）
- ツール呼び出しの逐次承認 UI。CLI が端末で聞いてきたら端末ドロワーで答える
- 差分の適用・取り消し、コミット・マージ・push。変更ビューは読むだけ（git へ書くのは
  worktree の追加・削除とブランチ作成だけ）
- ファイルの編集。ビュアーは読むだけ（「開く」で既定のアプリへ）
