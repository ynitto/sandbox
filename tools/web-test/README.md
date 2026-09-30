# web-test

Web アプリのテストを、条件の文章からテストケースファイルを作って実行するツール。

- **作る**: 条件を書くと、エージェント（Kiro CLI / GitHub Copilot CLI）がテストケースファイル（YAML）を書く。
  対象の URL を渡すと、実際にその画面を開いて取った要素の一覧もエージェントに渡すので、画面に無いボタン名を
  想像で書かれにくい。`--explore` を付けると、エージェントが playwright-cli で画面を操作して先の画面まで確かめてから書く。
  書式の誤りは検査で見つけ、エラーを添えて直してもらう
- **動かす**: Playwright でケースを実行し、操作のたびのスクリーンショットと、合否・失敗理由をまとめた
  レポート（HTML / Markdown / JSON）を出す。同じケースを Playwright Test の `.spec.ts` に書き出して
  `npx playwright test` で動かすこともできる
- **撮る**: 仕様書に貼る画面の画像を、同じ書式で決まった名前のファイルに書き出す

## 入れる

Windows（PowerShell）:

```powershell
powershell -ExecutionPolicy Bypass -File tools\web-test\install.ps1 -Check
```

Linux / macOS / WSL:

```bash
tools/web-test/install.sh --check
```

足りないものだけを入れる。Node.js 18 以上が無ければ公式の LTS を利用者のフォルダに入れ（管理者権限は要らない）、
npm パッケージ（playwright・@playwright/test・@playwright/cli・yaml）と Playwright の Chromium を入れて、
`web-test` コマンドを置く（Windows は `%LOCALAPPDATA%\web-test\bin`、ほかは `~/.local/bin`）。
`-Check` / `--check` は入れたあとに同梱のサンプルでテストを 1 回動かす。
Linux でブラウザが OS のライブラリ不足で起動しないときは `install.sh --with-deps`（sudo を使う）。

エージェントで作るときは、使う CLI（`kiro-cli` か `copilot`）を入れてログインしておく。インストーラは有無を知らせるだけで入れない。

## 使い方

### 1. テストケースを作る

```bash
web-test generate "ログイン画面。正しいパスワードで商品一覧に進むこと、違うとエラーが出ること" \
  --url http://localhost:3000/ -o tests/login.yaml
```

- `--agent kiro`（既定）か `--agent copilot` を選ぶ。ほかの CLI は `--agent-cmd "<コマンド>"` で渡せる。
  依頼の本文はファイルに書き、そのファイルを読むよう頼む指示を最後の引数で渡す
- `--explore` を付けると、web-test が playwright-cli でブラウザを開いておき、エージェントがそのブラウザを
  操作して（ログインして次の画面へ進むなど）要素を確かめながら書く。画面をまたぐ条件のときに使う。
  エージェントにはシェルのコマンド実行を許す起動形になる
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
- `variants` で同じケースを言語・画面幅を変えて繰り返せる（ブラウザの言語とアプリの言語は別々に指定する）
- 画像を操作のたびに撮るか（`step`）、失敗時だけか（`failure`）はファイルの `screenshot` か `--screenshot` で決める
- 失敗したときは、その時点の画面全体を撮る。ブラウザのコンソールエラーもレポートに出す
- レポートの「実行記録」に、実行したコマンド・環境・Node と Playwright の版・テストケースファイルのハッシュ・
  関係するリポジトリのコミットを残す。仕様や実装が別のフォルダにあるときは `--source <フォルダ>` で足す

### 3. Playwright Test で動かす

```bash
web-test pwtest tests/ -- --workers=4 --retries=1     # 書き出してそのまま npx playwright test
web-test export tests/ --out e2e/                      # 書き出すだけ（npx playwright test --config e2e/playwright.config.ts）
```

YAML から `.spec.ts` と `playwright.config.ts` を書き出す。ケースは `test`、ステップは `test.step`、
スクリーンショットはレポートの添付になり、失敗時は trace が残る（`npx playwright show-trace`）。
`--` の後ろは `npx playwright test` の引数としてそのまま渡る。書き出したファイルは手で直さず、YAML を直して書き出し直す。

自前の実行（`run`）と Playwright Test のどちらを使うか:

| | `web-test run` | `web-test pwtest` |
|---|---|---|
| 操作ごとの画像 | 標準で残る | 標準で残る（レポートの添付） |
| 失敗の調べ方 | 失敗時の画像・コンソールエラー | trace viewer で 1 操作ずつ巻き戻せる |
| リトライ・分割実行 | なし | `--retries` `--shard` など Playwright Test の機能 |
| レポート | 日本語の HTML・Markdown・JSON | Playwright の HTML レポート |

### 4. 仕様書の画像を撮る

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

名前が決まっているので、撮り直しても仕様書のリンクは変わらない。`variants` があるときは `login-form.en.png` のように
組の名前が付く。`path:` を書けば `run` のときにもその場所へ写す。
時刻など毎回変わる部分は `mask` で塗るか、`eval` で固定の文字に置き換えてから撮る。

## 環境を切り替える（ローカル・検証環境）

カレントディレクトリに `web-test.config.yaml` を置き、環境ごとの接続先・認証・事前に入れる値を書く。
値の `${名前}` は環境変数で置き換えるので、トークンなどはファイルに書かない。

```yaml
defaultEnv: local
envs:
  local:
    baseUrl: http://localhost:3000
  staging:
    baseUrl: https://stg.example.com
    mocks: false                              # モックを使うケースは動かさない
    storageState: auth/staging.json           # ログイン済みの状態（Playwright の storageState）
    headers: { Authorization: "Bearer ${STAGING_TOKEN}" }
```

```bash
web-test run tests/ --env staging
```

環境に書けるのは `baseUrl` `localStorage` `sessionStorage` `cookies` `headers` `storageState` `mocks` `locale` `timezone`。
スタブに頼る異常系のケースには `envs: [local]` を付けておくと、検証環境では飛ばす。

## 例

`examples/sample-app` に小さな Web アプリ、`examples/login.yaml` にそのテストケースがある。

```bash
node examples/sample-app/server.js 3000 &
web-test run examples/login.yaml
web-test pwtest examples/login.yaml
```

## ほかの Playwright の道具と組み合わせる

- セレクタを探すとき: `web-test snapshot <URL>` で画面の要素一覧（役割と名前）を見る。
  `npx playwright codegen <URL>` で操作を記録して、出てきた `getByRole(...)` をこの書式の `{ role, name }` に写してもよい
- ブラウザを表示して動きを見たいとき: `web-test run ... --headed`

## テスト

```bash
npm test
```
