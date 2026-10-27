# svg-clip-image-editor

SVG の中のクリップされた画像を、枠を動かさずに画像の拡大縮小・回転・移動（マウス・タッチ・キー）で枠に合わせ込む
React コンポーネントです。結果は、元の SVG と同じ座標系のまま、画像の `transform` だけを書き換えた SVG として受け取れます。

**サンプル実装です。** 設計は [設計書](../../docs/designs/svg-clip-image-editor-design.md) にあり、未実装の項目は末尾にまとめています。

## 使い方

```js
import { ClipEditorToolbar, SvgClipImageEditor, useClipEditor } from 'svg-clip-image-editor';
import 'svg-clip-image-editor/style.css'; // 任意。読み込まなければ素の見た目

const editor = useClipEditor();

<SvgClipImageEditor
  ref={editor.ref}
  onStateChange={editor.onStateChange}        // 元に戻せるか・枠を覆えているか などを外に出す
  svg={svgText}                               // 下の「SVG の書き方」を満たす文字列
  slot="hero"                                 // 編集する画像の名前（省略時は最初の 1 つ）
  onCommit={(svg, { slot, M }) => save(svg)}  // 画像が止まって確定するたびに呼ばれる
/>
<ClipEditorToolbar editor={editor} />         // 任意。使わなければ自前のボタンで組む
```

- コンポーネントが描くのは描画面（SVG）だけです。背景は透明で、置いた場所の背景がそのまま見えます。
- ボタン列は別の部品 `ClipEditorToolbar` です。置く場所は自由で、使わずに自前のボタンを組むこともできます。
  そのときは ref の操作（下）を呼び、`onStateChange` の `{ canUndo, canRedo, covered, moving }` で有効・無効や警告を出します。
- 画像の切り替えの入口（一覧・タブなど）と保存ボタンも、呼び出す側に置きます。`onLoad` で画像の一覧を受け取れます。
- 実行時に必要なのは `react` と `react-dom` だけです。

## 見た目を変える

見た目は `style.css` とクラス名だけで決めています。コンポーネントのインラインスタイルには、動きに要るもの（ページのスクロールを止める、縦横比など）しか書いていません。

| クラス・属性 | 付く所 |
|---|---|
| `.sce-surface` | 描画面。キーボードで入ったときは `[data-focus-ring="true"]`、枠を覆えていない間は `[data-covered="false"]` が付く |
| `.sce-frame` | 枠の点線 |
| `.sce-image-outside` / `.sce-image-inside` | 枠の外の半透過の画像（既定の不透明度 0.35。`opacity` で変えられる）/ 枠の中の画像 |
| `.sce-placeholder` / `.sce-error` | 読み込み前 / 読めなかったとき |
| `.sce-toolbar` / `.sce-button` / `.sce-toolbar-spacer` | 既定のボタン列 |

色は CSS 変数 `--clip-editor-frame` / `--clip-editor-warn` / `--clip-editor-focus` / `--clip-editor-button-border` / `--clip-editor-button-bg` / `--clip-editor-button-fg` を、置いた場所の祖先で指定して変えられます。

## SVG の書き方

編集する画像には `data-clip-slot="名前"` を付けた `<g>` にクリップを持たせ、その唯一の子に `<image>` を置きます。

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600">
  <defs>
    <clipPath id="hero-clip"><path d="M200 150 H600 V450 H200 Z"/></clipPath>
  </defs>
  <g data-clip-slot="hero" clip-path="url(#hero-clip)">
    <image href="photo.jpg" x="0" y="0" width="1200" height="800" transform="matrix(0.5 0 0 0.5 100 100)"/>
  </g>
  <!-- ほかの飾りは自由 -->
</svg>
```

守らなければいけない規則（祖先は `<g>` だけ、クリップの中は図形だけ、など）は設計書の §1.1 にあります。
満たしていないときは、エディタの代わりに 1 行のメッセージが出て、`onError` に違反の一覧が渡ります。

## 操作

| 操作 | 動き |
|---|---|
| ドラッグ（1 本指） | 移動。速く弾くと滑る |
| 2 本指 | 拡大縮小・回転・移動 |
| ホイール / トラックパッドのピンチ | 拡大縮小（Shift を押しながらで回転） |
| 矢印キー（Shift で大きく） | 移動 |
| `+` / `-`、`[` / `]`（Shift で 15°） | 拡大縮小、回転 |

2 本指の回転は、90° の倍数から ±2° 以内なら吸い付きます（拡大縮小のときに指がねじれても傾かないように）。少しだけ傾けたいときは `[` / `]` で 1° ずつ回します。
元に戻す・やり直すはキーでは受けません。置いたアプリの「元に戻す」とぶつからないよう、外から ref の `undo()` / `redo()` を呼びます。

枠の内側に画像のない所ができると、枠の線が赤くなり（`style.css` を読み込んだとき）、指を離すと覆える位置へ戻ります。
`ClipEditorToolbar` の文言は `labels` で差し替えられます。

## 主なプロパティ

画像の最初の位置は、SVG に書かれている `transform` です。枠を画像で覆う制約は常にかかります。

| プロパティ | 既定 | 意味 |
|---|---|---|
| `rotationCover` | `'rubber'` | 回転で覆えなくなったとき。`'rubber'` は離してから戻す、`'grow'` は回転中も即座に拡大して覆う |
| `motion` | 下の表 | 端での動きの定数。**数値をすべて 0 にすると、端に当たった瞬間に止まる** |
| `focusPadding` | `0.25` | 枠の周りに見せる余白（枠の大きさに対する比） |
| `minScale` / `maxScale` | `0.1` / `20` | 読み込み時に対する拡大率の範囲 |
| `ariaLabel` | 「画像の位置を調整」 | 描画面の読み上げ名 |
| `onStateChange` | — | 状態 `{ canUndo, canRedo, covered, moving }` が変わったとき |
| `className` / `style` | — | 描画面の `<svg>` に足す |

`motion` のキー（秒・比）:

| キー | 既定 | 0 のとき |
|---|---|---|
| `rubber` | 0.55 | 端を越えない |
| `rubberLimit` | 0.25 | 端を越えない |
| `scaleRubberLimit` | 0.5 | 覆える大きさより縮まない |
| `friction` | 0.325 | 弾いても滑らない |
| `flingSpeed` | 0.3 | — |
| `springPeriod` | 0.4 | 即座に戻る |
| `snapDuration` | 0.2 | ボタン・キーの動きが即座 |
| `wheelSettleDelay` | 0.15 | ホイールのたびに即座に戻す |

ref から `getSvg()` / `getTransforms()` / `getState()` / `reset()` / `fit()` / `zoom(f)` / `rotate(deg)` / `undo()` / `redo()` を呼べます。

React を使わない部分は `svg-clip-image-editor/core` から `parseClipDocument`（解析）・`lintDocument`（規則の検査）・`serialize`（出力）を読めます。Node でも動きます
（`DOMParser` は jsdom などから渡します）。

## デモ

```
npm install
npm run demo:build      # demo/dist/main.js を作る
# demo/index.html をブラウザで開く
npm run demo:shot -- <出力先>   # Chromium で操作前後のスクリーンショットを撮る（Playwright が要る）
```

## テスト

```
npm test
```

## サンプルで未実装のもの

- 既存の SVG を規則の形に書き換える取り込み（`adoptClipDocument`）
- 飾りを画像の上下に重ねて見せる表示（`context`）
- 規則を検査するコマンド（設計のみ）
- `rotationCover="grow"` は、端で止まって基準を取り直したあと（すべて 0 のときなど）、回転を戻しても拡大が戻らないことがある
