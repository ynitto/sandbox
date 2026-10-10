# office-preview

Word（docx）・Excel（xlsx）・PowerPoint（pptx）のファイルから、プレビュー用の画像（PNG / JPEG）を作るモジュールです。
Electron アプリの main プロセスから呼び出します。実行時に追加のパッケージは要りません。Office も LibreOffice も使いません。

| 種類 | 画像になる範囲 | 既定の大きさ（CSS px） |
|---|---|---|
| docx | 1 ページ目 | 用紙の大きさ（A4 縦なら 794×1123） |
| xlsx | Excel で開いたときに表示されるシートの左上 | 1024×640 |
| pptx | 1 枚目のスライド（`slide` で選べる） | スライドの大きさ（16:9 なら 1280×720） |

同じ系統の docm・dotx・xlsm・xltx・pptm・potx・ppsx も読めます。

## 使い方

```js
// main プロセス
const { app, ipcMain } = require('electron');
const preview = require('office-preview'); // または require('<このフォルダ>/src')

app.whenReady().then(() => {
  ipcMain.handle('preview:office', async (_event, file) => {
    // 渡されたパスがアプリの扱う範囲（リポジトリの中など）にあるかは、呼ぶ側で確かめる
    if (!preview.supports(file)) return null;
    try {
      const { data, mime, width, height } = await preview.renderPreview(file, { width: 480 });
      return { src: `data:${mime};base64,${data.toString('base64')}`, width, height };
    } catch (err) {
      return { error: err.code }; // 例: ENCRYPTED_OR_LEGACY → 「外部アプリで開く」へ
    }
  });
});
```

### `renderPreview(input, options)` → `Promise<{ data, mime, width, height, type, ... }>`

`input` はファイルのパスか、ファイルの中身の `Buffer` です。種類は拡張子でなく中身で判断します。

| オプション | 意味 | 既定 |
|---|---|---|
| `width` | 出力する画像の幅（px）。高さは縦横比で決まる | 原寸 |
| `scale` | `width` の代わりに倍率で指定する | 1 |
| `format` | `'png'` か `'jpeg'` | `'png'` |
| `quality` | JPEG の品質（0〜100） | 85 |
| `slide` | pptx の何枚目か（0 始まり） | 0 |
| `sheet` | xlsx のシート（0 始まりの番号か、シート名） | 開いたときに表示されるシート |
| `viewport` | xlsx で切り取る大きさ `{ width, height }`（CSS px） | `{ width: 1024, height: 640 }` |
| `headers` | xlsx の列見出し（A, B, …）と行番号を描くか | `true` |
| `timeoutMs` | これを過ぎたら `TIMEOUT` で止める | 20000 |

返り値には、pptx なら `slide` と `slideCount`、xlsx なら `sheet`（シート名）も入ります。

`app` の準備ができる前に呼ぶと、準備ができるまで待ちます。同時に描くのは既定で 2 件までで、残りは順番を待ちます
（`setConcurrency(n)` で変えられます）。

### そのほかの関数

- `toHtml(input, options)` … 画像にする前の HTML を返します（Electron がなくても動きます）。見た目を調べるときに使います。
- `readEmbeddedThumbnail(input)` … 保存時に「プレビューの画像を保存する」を選んだファイルに入っている
  サムネイルを `{ mime, data }` で返します。無ければ `null` です。小さい画像ですが、描くより速く取り出せます。
- `supports(path)` … 拡張子が対象かどうか。

### 失敗したとき

`OfficePreviewError` を投げます。`code` で分岐してください。

| `code` | 意味 |
|---|---|
| `ENCRYPTED_OR_LEGACY` | パスワード付き、または古い形式（.doc / .xls / .ppt） |
| `NOT_ZIP` / `BROKEN` | ファイルが壊れている |
| `UNSUPPORTED` | docx / xlsx / pptx ではない |
| `TOO_LARGE` | ファイル（200 MB まで）や中身の展開後の大きさが上限を超えた |
| `TIMEOUT` | 時間内に描き終わらなかった |
| `NO_ELECTRON` | Electron の main プロセスの外で `renderPreview` を呼んだ |

## 描くもの・描かないもの

Office と同じ見た目にはなりません。何のファイルかが一目で分かることを目指しています。

| | 描くもの | 描かないもの |
|---|---|---|
| docx | 文字の書式とスタイル、段落の配置・間隔・インデント・罫線、箇条書きと段落番号、行グリッド、表（結合・罫線・塗り）、画像、テキストボックス、ヘッダーとフッター、ページの色 | 自動の改ページ位置の計算（1 ページの高さで切る）、段組み、脚注、コメント、変更履歴の削除側、数式の組版、図形（テキストボックス以外） |
| xlsx | 列幅と行の高さ、フォント・塗り・罫線・配置、表示形式（桁区切り・小数・%・日付・時刻・通貨・負の数の色）、セルの結合、右隣への文字のはみ出し、シート上の画像 | グラフ、条件付き書式、図形、ふりがな、数式の再計算（保存された値を出す） |
| pptx | レイアウトとマスターから引き継ぐ位置・書式・背景、テーマの色とフォント、図形（四角・角丸・楕円・矢印・多角形・中かっこ・円弧・ドーナツ・自由形状）、塗り（単色・グラデーション・画像）、線（破線・グラデーション・矢印）、文字と箇条書き、画像（トリミング、SVG）、表（組み込みの既定のスタイルを含む）、グループ、SmartArt | グラフ（枠だけ描く）、影・光彩などの効果、3D、動画の再生画面（代わりの画像は描く）、EMF / WMF の画像 |

フォントは文書の指定どおりに頼みますが、その PC に無いフォントは游ゴシック・メイリオなどで代わりに描きます。

## 安全のために

文書は信頼できない入力として扱います。

- 描画は専用の隠しウィンドウで行い、スクリプトを止め、Node の機能も渡しません。
- 外への通信はすべて止めます。画像は文書の中のものだけを使います。
- ZIP の展開後の大きさに上限を設けています（部品 1 つで 64 MB、全体で 256 MB）。
- XML の外部実体（DTD）は読みません。

## 試す

```sh
npm install
npx electron scripts/render.js 資料.pptx 資料.png 640   # 幅 640 px で書き出す（Linux で画面が無ければ xvfb-run を付ける）
npm test                                             # 画面が無い環境では、実際に描くテストだけ飛ばす
```
