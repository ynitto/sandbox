# svg-clip-image-editor

SVG の中のクリップされた画像を、枠を動かさずに画像の拡大縮小・回転・移動（マウス・タッチ・キー）で枠に合わせ込む
React コンポーネントです。結果は、元の SVG と同じ座標系のまま、画像の `transform` だけを書き換えた SVG として受け取れます。

**サンプル実装です。** 設計は [設計書](../../docs/designs/svg-clip-image-editor-design.md) にあり、未実装の項目は末尾にまとめています。

## 使い方

```js
import { SvgClipImageEditor } from 'svg-clip-image-editor';

<SvgClipImageEditor
  svg={svgText}                       // 下の「SVG の書き方」を満たす文字列
  slot="hero"                         // 編集する画像の名前（省略時は最初の 1 つ）
  onCommit={(svg, { slot, M }) => save(svg)}  // 画像が止まって確定するたびに呼ばれる
/>
```

- 画像の切り替えの入口（一覧・タブなど）と保存ボタンは、呼び出す側に置きます。`onLoad` で画像の一覧を受け取れます。
- 実行時に必要なのは `react` と `react-dom` だけです。グローバルな CSS は持ちません。色は CSS 変数
  （`--clip-editor-frame` / `--clip-editor-warn` / `--clip-editor-focus` / `--clip-editor-bg` / `--clip-editor-checker-a` / `--clip-editor-checker-b` / `--clip-editor-button-*`）で変えられます。

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
| Ctrl/⌘ + Z、Shift 付きでやり直し | 元に戻す |

枠の内側に画像のない所ができると、枠の線が赤くなり、指を離すと覆える位置へ戻ります。

## 主なプロパティ

| プロパティ | 既定 | 意味 |
|---|---|---|
| `cover` | `true` | 枠を画像で覆う制約 |
| `rotationCover` | `'rubber'` | 回転で覆えなくなったとき。`'rubber'` は離してから戻す、`'grow'` は回転中も即座に拡大して覆う |
| `motion` | 下の表 | 端での動きの定数。**数値をすべて 0 にすると、端に当たった瞬間に止まる** |
| `transforms` / `defaultTransforms` | — | 画像の名前 → 行列（`[a, b, c, d, e, f]`）。`transforms` を渡すと親が値を持つ |
| `focusPadding` | `0.25` | 枠の周りに見せる余白（枠の大きさに対する比） |
| `outsideOpacity` | `0.35` | 枠の外の画像の不透明度 |
| `minScale` / `maxScale` | `0.1` / `20` | 読み込み時に対する拡大率の範囲 |
| `rotationSnap` | `0` | 2 本指の回転を 90° の倍数に吸着させる幅（度） |
| `toolbar` / `labels` | `true` / 日本語 | 下のボタン列と、その文言 |
| `precision` / `outputTransform` | `3` / `'matrix'` | 出力の書式 |

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

ref から `getSvg()` / `getTransforms()` / `reset()` / `fit()` / `zoom(f)` / `rotate(deg)` / `undo()` / `redo()` を呼べます。

React を使わない部分（解析・規則の検査・出力）は `svg-clip-image-editor/core` から読めます。Node でも動きます
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
