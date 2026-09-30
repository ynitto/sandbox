# web-test

Web アプリのテストを、条件の文章からテストケースファイルを作って実行するツール。

- **作る**: 条件を書くと、エージェント（Kiro CLI / GitHub Copilot CLI）がテストケースファイル（YAML）を書く。
  対象の URL を渡すと、実際にその画面を開いて取った要素の一覧もエージェントに渡すので、画面に無いボタン名を
  想像で書かれにくい。書式の誤りは検査で見つけ、エラーを添えて直してもらう
- **動かす**: Playwright でケースを実行し、操作のたびのスクリーンショットと、合否・失敗理由をまとめた
  レポート（HTML / Markdown / JSON）を出す
- **撮る**: 仕様書に貼る画面の画像を、同じ書式で決まった名前のファイルに書き出す

## 準備

Node.js 18 以上が要る。

```bash
cd tools/web-test
npm install
npx playwright install chromium     # Linux で依存ライブラリも要るときは --with-deps
npm link                            # 任意。web-test コマンドとして使えるようにする
```

エージェントで作るときは、使う CLI にログインしておく（`kiro-cli login` / `copilot` の初回ログイン）。

## 使い方

### 1. テストケースを作る

```bash
web-test generate "ログイン画面。正しいパスワードで商品一覧に進むこと、違うとエラーが出ること" \
  --url http://localhost:3000/ -o tests/login.yaml
```

- `--agent kiro`（既定）か `--agent copilot` を選ぶ。ほかの CLI は `--agent-cmd "<コマンド>"` で渡せる。
  依頼の本文はファイルに書き、そのファイルを読むよう頼む指示を最後の引数で渡す
- 条件が長いときは `-f conditions.md` でファイルから読む
- 今あるファイルを直す・ケースを足すときは `--update`
- チャット画面のエージェント（GitHub Copilot のチャットなど）で作るときは、`web-test prompt "<条件>" --url <URL>` が
  出す依頼文を貼り、返ってきた YAML を保存して `web-test validate` で確かめる

作ったファイルは人が読んで直せる。書式は `web-test format` か [src/format-reference.md](src/format-reference.md)。

### 2. 実行する

```bash
web-test run tests/                                   # ディレクトリ内の .yaml / .yml / .json をすべて
web-test run tests/login.yaml --base-url http://localhost:5173 --workers 4
```

`web-test-results/<日時>/report.html` をブラウザで開くと、ケースごとに合否・失敗理由・操作ごとの画像が見られる。
`report.md` は課題やレビューに貼る用、`results.json` はほかのツールで読む用。
すべて合格なら終了コード 0、不合格があれば 1 なので、CI にもそのまま載せられる。

- ケースごとに新しいブラウザの状態で始まる。`localStorage` `cookies` はアプリの読み込みより先に入れる
- `mocks` で API の応答を差し替えられる（500 を返す、1 回目だけ失敗させる、遅らせる、切断する）
- 画像を操作のたびに撮るか（`step`）、失敗時だけか（`failure`）はファイルの `screenshot` か `--screenshot` で決める
- 失敗したときは、その時点の画面全体を撮る。ブラウザのコンソールエラーもレポートに出す

### 3. 仕様書の画像を撮る

```yaml
suite: 仕様書の画像
baseUrl: http://localhost:3000
viewport: { width: 1280, height: 800 }
cases:
  - id: S-01
    title: ログイン画面
    steps:
      - goto: /login
      - fill: { target: { label: メールアドレス }, value: user@example.com }
      - screenshot: { name: login-form, target: { css: main }, mask: [{ css: .clock }] }
```

```bash
web-test capture docs/screens.yaml --out docs/images      # docs/images/login-form.png ができる
```

名前が決まっているので、撮り直しても仕様書のリンクは変わらない。`path:` を書けば `run` のときにもその場所へ写す。
時刻など毎回変わる部分は `mask` で塗るか、`eval` で固定の文字に置き換えてから撮る。

## 例

`examples/sample-app` に小さな Web アプリ、`examples/login.yaml` にそのテストケースがある。

```bash
node examples/sample-app/server.js 3000 &
web-test run examples/login.yaml
```

## ほかの Playwright の道具と組み合わせる

- セレクタを探すとき: `web-test snapshot <URL>` で画面の要素一覧（役割と名前）を見る。
  `npx playwright codegen <URL>` で操作を記録して、出てきた `getByRole(...)` をこの書式の `{ role, name }` に写してもよい
- ブラウザを表示して動きを見たいとき: `web-test run ... --headed`

## テスト

```bash
npm test
```
