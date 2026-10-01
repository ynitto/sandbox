# webui-test

Web アプリのテストを、条件の文章からテストケースファイルを作って実行するツール。

- **作る**: 条件を書くと、エージェント（Kiro CLI / GitHub Copilot CLI）がテストケースファイル（YAML）を書く。
  対象の URL を渡すと、実際にその画面を開いて取った要素の一覧もエージェントに渡すので、画面に無いボタン名を
  想像で書かれにくい。`--explore` を付けると、エージェントが playwright-cli で画面を操作して先の画面まで確かめてから書く。
  書式の誤りは検査で見つけ、エラーを添えて直してもらう
- **動かす**: Playwright でケースを実行し、操作のたびのスクリーンショットと、合否・失敗理由をまとめた
  レポート（HTML / Markdown / JSON）を出す。同じケースを Playwright Test の `.spec.ts` に書き出して
  `npx playwright test` で動かすこともできる
- **撮る**: 仕様書に貼る画面の画像を、同じ書式で決まった名前のファイルに書き出す
- **そろえる**: `webui-test check` が、単体テスト・アプリをローカルで起動しての e2e・仕様書の画像が今の画面と同じか・
  仕様書の画像のリンクを 1 回で確かめる。[codd-statemachine](../codd-statemachine/README.md) の検査に入れると、
  実装・テスト・仕様書を変えるたびにずれを止める

## 入れる

Windows（PowerShell）:

```powershell
powershell -ExecutionPolicy Bypass -File tools\webui-test\install.ps1 -Check
```

Linux / macOS / WSL:

```bash
tools/webui-test/install.sh --check
```

足りないものだけを入れる。Node.js 18 以上が無ければ公式の LTS を利用者のフォルダに入れ（管理者権限は要らない）、
npm パッケージ（playwright・@playwright/test・@playwright/cli・yaml・画像を比べる pngjs と pixelmatch）と Playwright の Chromium を入れて、
`webui-test` コマンドを置く（Windows は `%LOCALAPPDATA%\webui-test\bin`、ほかは `~/.local/bin`）。
`-Check` / `--check` は入れたあとに同梱のサンプルでテストを 1 回動かす。
Linux でブラウザが OS のライブラリ不足で起動しないときは `install.sh --with-deps`（sudo を使う）。

エージェントで作るときは、使う CLI（`kiro-cli` か `copilot`）を入れてログインしておく。インストーラは有無を知らせるだけで入れない。

## 使い方

### 1. テストケースを作る

```bash
webui-test generate "ログイン画面。正しいパスワードで商品一覧に進むこと、違うとエラーが出ること" \
  --url http://localhost:3000/ -o tests/login.yaml
```

- `--agent kiro`（既定）か `--agent copilot` を選ぶ。ほかの CLI は `--agent-cmd "<コマンド>"` で渡せる。
  依頼の本文はファイルに書き、そのファイルを読むよう頼む指示を最後の引数で渡す
- `--explore` を付けると、webui-test が playwright-cli でブラウザを開いておき、エージェントがそのブラウザを
  操作して（ログインして次の画面へ進むなど）要素を確かめながら書く。画面をまたぐ条件のときに使う。
  エージェントにはシェルのコマンド実行を許す起動形になる
- 条件が長いときは `-f conditions.md` でファイルから読む
- 仕様書があるときは `--doc docs/login.md` で渡す。中身を依頼に入れ、ケースファイルの先頭に
  `# coherence: doc=docs/login.md` と書く（実装は `--code src/login.tsx`）。この書き方は codd-statemachine がたどる
- 今あるファイルを直す・ケースを足すときは `--update`
- チャット画面のエージェント（GitHub Copilot のチャットなど）で作るときは、`webui-test prompt "<条件>" --url <URL>` が
  出す依頼文を貼り、返ってきた YAML を保存して `webui-test validate` で確かめる

作ったファイルは人が読んで直せる。書式は `webui-test format` か [src/format-reference.md](src/format-reference.md)。

### 2. 実行する

```bash
webui-test run tests/                                   # ディレクトリ内の .yaml / .yml / .json をすべて
webui-test run tests/login.yaml --base-url http://localhost:5173 --workers 4
```

`webui-test-results/<日時>/report.html` をブラウザで開くと、ケースごとに合否・失敗理由・操作ごとの画像が見られる。
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
webui-test pwtest tests/ -- --workers=4 --retries=1     # 書き出してそのまま npx playwright test
webui-test export tests/ --out e2e/                      # 書き出すだけ（npx playwright test --config e2e/playwright.config.ts）
```

YAML から `.spec.ts` と `playwright.config.ts` を書き出す。ケースは `test`、ステップは `test.step`、
スクリーンショットはレポートの添付になり、失敗時は trace が残る（`npx playwright show-trace`）。
`--` の後ろは `npx playwright test` の引数としてそのまま渡る。書き出したファイルは手で直さず、YAML を直して書き出し直す。

自前の実行（`run`）と Playwright Test のどちらを使うか:

| | `webui-test run` | `webui-test pwtest` |
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
webui-test capture docs/screens.yaml --out docs/images      # docs/images/login-form.png ができる
```

名前が決まっているので、撮り直しても仕様書のリンクは変わらない。`variants` があるときは `login-form.en.png` のように
組の名前が付く。`path:` を書けば `run` のときにもその場所へ写す。
時刻など毎回変わる部分は `mask` で塗るか、`eval` で固定の文字に置き換えてから撮る。

## 実装・テスト・仕様書をそろえる（check）

`webui-test.config.yaml` に、アプリの起動のしかたと、確かめるものを書く。

```yaml
serve:                                   # テストの前にローカルで起動し、終わったら止める
  command: npm start
  url: http://localhost:3000             # 応答するまで待つ。すでに応答していれば起動しない
captureRoot: .                           # screenshot の path: の起点（既定はこのファイルのフォルダ）
check:
  unit: npm test                         # 単体テスト（任意）
  cases: [tests/e2e]                     # e2e のケース
  docs: [docs]                           # 画像を貼っている仕様書のフォルダ（任意）
envs:
  local: {}
```

```bash
webui-test check            # ずれがあれば終了コード 1
webui-test check --update   # 画面の変更が意図どおりなら、仕様書の画像を撮り直す
```

1. 単体テスト（`check.unit`）を動かす
2. アプリを起動して、e2e のケースを動かす（レポートは `webui-test-results/check-<日時>/`）
3. `screenshot` ステップの `path:` にある仕様書の画像と、いま撮った画面を比べる。違えば落とし、差分の画像をレポートに残す。
   画像は書き換えない（`--update` のときだけ撮り直す）
4. `check.docs` のマークダウンが貼っている画像が実在するかを確かめる。撮っているのにどの仕様書も貼っていない画像は知らせるだけ

結果は出力の最後にまとめる。画像は 1 画素でも違えば落とす（色の近さは許容する）。PC ごとの描画の差で揺れるときは
`check.maxDiffRatio: 0.001` のように違ってよい割合を書く。

`serve` は `run`・`capture`・`pwtest` でも使う。接続先（`baseUrl`）が `serve` の URL と違う環境（検証環境など）では起動しない。
環境ごとに変えるときは `envs.<名前>.serve` に書く（`false` で起動しない）。

### codd-statemachine と組む

[codd-statemachine](../codd-statemachine/README.md) は、実装と仕様書を突き合わせて計画を立て、確認してから両方を変える。
その「変えたあとの検査」に `webui-test check` を入れると、変えるたびに単体テスト・e2e・仕様書の画像を確かめる。

```bash
python3 tools/codd-statemachine/install.py ~/work/my-app --side impl --ref docs=../my-app-docs --check "webui-test check"
```

- ケースファイルの先頭に、確かめている仕様書と実装を書く。仕様書を変える計画はこのケースファイルも扱わないと通らない

  ```yaml
  # coherence: doc=docs:docs/specs/login.md
  # coherence: code=src/pages/login.tsx
  suite: ログイン画面
  ```

  仕様書が別のリポジトリにあるときは `参照先の名前:パス`（codd-statemachine の `refs` の名前）で書く
- 仕様書の画像が別のリポジトリにあるときは、`captureRoot` をそのリポジトリにして `path:` を書く
- 画面を変えたら、撮り直す画像も計画に挙げ、変えたあとに `webui-test check --update` で撮り直す

## 環境を切り替える（ローカル・検証環境）

カレントディレクトリに `webui-test.config.yaml` を置き、環境ごとの接続先・認証・事前に入れる値を書く。
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
webui-test run tests/ --env staging
```

環境に書けるのは `baseUrl` `localStorage` `sessionStorage` `cookies` `headers` `storageState` `mocks` `locale` `timezone` `serve`。
スタブに頼る異常系のケースには `envs: [local]` を付けておくと、検証環境では飛ばす。

## 例

`examples/sample-app` に小さな Web アプリ、`examples/login.yaml` にそのテストケースがある。

```bash
node examples/sample-app/server.js 3000 &
webui-test run examples/login.yaml
webui-test pwtest examples/login.yaml
```

## ほかの Playwright の道具と組み合わせる

- セレクタを探すとき: `webui-test snapshot <URL>` で画面の要素一覧（役割と名前）を見る。
  `npx playwright codegen <URL>` で操作を記録して、出てきた `getByRole(...)` をこの書式の `{ role, name }` に写してもよい
- ブラウザを表示して動きを見たいとき: `webui-test run ... --headed`

## テスト

```bash
npm test
```
