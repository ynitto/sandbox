# テストケースファイルの書式

YAML（または JSON）で書く。1 ファイル = 1 スイート（画面や機能のまとまり）。

```yaml
suite: ログイン画面                  # スイート名（レポートの見出し）
baseUrl: http://localhost:3000       # goto の相対パスの起点（実行時 --base-url で上書きできる）
browser: chromium                    # chromium / firefox / webkit（省略時 chromium）
viewport: { width: 1280, height: 800 }
locale: ja-JP                        # 省略可。ブラウザの言語
timezone: Asia/Tokyo                 # 省略可
timeout: 10000                       # 1 ステップの待ち時間（ミリ秒）
screenshot: step                     # step=操作のたびに撮る / failure=失敗時だけ / off=撮らない
                                     # （screenshot ステップは常に撮る）
setup:                               # すべてのケースの前に行う（省略可）
  localStorage: { featureFlags: { newUi: true } }   # 値が文字列以外なら JSON にして入れる
  mocks:
    - { url: "**/api/config", json: { theme: light } }
  steps:
    - goto: /
cases:
  - id: TC-001                       # 必須・スイート内で一意
    title: 正しい資格情報でログインできる   # 必須
    requirement: REQ-AUTH-01         # 省略可。要件 ID
    tags: [smoke]                    # 省略可
    steps:
      - goto: /login
      - fill: { target: { label: メールアドレス }, value: user@example.com }
      - fill: { target: { label: パスワード }, value: secret }
      - click: { role: button, name: ログイン }
      - expect: { url: /dashboard }
      - expect: { visible: { text: ようこそ } }
      - screenshot: ダッシュボード
```

ケースには `viewport` `locale` `timezone` `colorScheme` `localStorage` `sessionStorage` `cookies`
`headers` `mocks` を書いてスイートの値を上書き・追加できる。`skip: 理由` でそのケースを飛ばす。
ケースごとに新しいブラウザ（コンテキスト）で始まるので、前のケースの状態は残らない。

## 対象（target）の書き方

画面の要素は、利用者から見える手がかりで指す。上から順に優先する。

| 書き方 | 意味 |
|---|---|
| `{ role: button, name: 保存 }` | 役割と名前（ボタン・リンク・見出し・textbox・checkbox など） |
| `{ label: メールアドレス }` | ラベルの付いた入力欄 |
| `{ placeholder: 検索 }` | プレースホルダ |
| `{ text: 保存しました }` | 表示されている文字（部分一致。`exact: true` で完全一致） |
| `{ testId: submit }` | `data-testid` 属性 |
| `{ alt: ロゴ }` / `{ title: 閉じる }` | 画像の代替テキスト / title 属性 |
| `{ css: "#login > button" }` | CSS セレクタ（ほかで指せないときだけ） |
| `"text=保存"` / `"#id"` | 文字列はそのまま Playwright のセレクタ |

同じ要素が複数あるときは `nth: 0` のように何番目か（0 始まり）を添える。

## ステップ

各ステップは 1 つのキーを持つ。`note:`（レポートに出す説明）と `timeout:` を添えてよい。

| ステップ | 例 |
|---|---|
| `goto` | `- goto: /login`（絶対 URL も可） |
| `reload` | `- reload: true` |
| `click` / `dblclick` / `hover` | `- click: { role: link, name: 設定 }` |
| `fill` | `- fill: { target: { label: 名前 }, value: 山田 }` |
| `select` | `- select: { target: { label: 都道府県 }, value: 東京都 }` |
| `check` / `uncheck` | `- check: { label: 同意する }` |
| `press` | `- press: Enter` / `- press: { target: { label: 検索 }, key: Enter }` |
| `upload` | `- upload: { target: { label: 添付 }, files: [fixtures/a.pdf] }`（ケースファイルからの相対） |
| `wait` | `- wait: 500`（ミリ秒）/ `- wait: { text: 完了 }`（見えるまで）/ `- wait: { hidden: { text: 読み込み中 } }` / `- wait: { url: /done }` / `- wait: { load: networkidle }` |
| `expect` | 下の表 |
| `screenshot` | `- screenshot: 入力後` / `- screenshot: { name: 一覧, fullPage: true, target: { css: main }, mask: [{ css: .clock }], path: docs/img/list.png }` |
| `eval` | `- eval: "document.querySelector('.clock').textContent = '12:00'"`（ページ内で JS を実行） |

### expect（確かめる）

条件が満たされるまで `timeout` の間待ってから判定する。

| 書き方 | 意味 |
|---|---|
| `{ visible: 対象 }` / `{ hidden: 対象 }` | 見えている / 見えていない |
| `{ target: 対象, text: 保存しました }` | 文字が一致（空白は 1 つにまとめて比べる。`/正規表現/` も可） |
| `{ target: 対象, contains: 保存 }` | 文字を含む |
| `{ target: 対象, value: abc }` | 入力欄の値 |
| `{ target: 対象, count: 3 }` | 要素の数 |
| `{ target: 対象, enabled: true }` / `disabled` / `checked` | 状態 |
| `{ url: /dashboard }` | URL にこの文字列を含む（`/正規表現/` か完全な URL も可） |
| `{ title: ホーム }` | ページタイトル |

## 通信のモック（mocks）

`setup.mocks` かケースの `mocks` に並べる。一致したリクエストにだけ偽の応答を返す。

```yaml
mocks:
  - { url: "**/api/items", method: GET, status: 500, json: { message: error } }
  - { url: "**/api/items", times: 1, status: 503 }        # 最初の 1 回だけ（再試行の確認）
  - { url: "**/api/slow", delayMs: 5000, json: [] }       # 遅延
  - { url: "**/api/save", abort: true }                    # 通信切断
```

キー: `url`（glob）・`method`・`status`・`json` / `body`・`headers`・`contentType`・`delayMs`・`abort`・`times`。

## 仕様書用のスクリーンショット

`screenshot` ステップに `path:` を書くと、実行時にその場所へも保存する（パスは `--capture-root`、既定は
カレントディレクトリからの相対）。`web-test capture` はレポートを作らず、`screenshot` ステップの画像だけを
`<出力先>/<name>.png` に書き出す。
