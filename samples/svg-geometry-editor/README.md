# SVG Geometry Editor

React + TypeScript の軽量な SVG ビューア兼画像幾何エディタです。画像のピクセルは加工せず、インライン SVG 内の単一の `<image>` を選択して位置・倍率・角度を編集します。画像以外のフレームや背景は固定です。MIT ライセンス。

## 実行

```bash
cd samples/svg-geometry-editor
npm install
npm run dev       # http://localhost:5173
npm test
npm run typecheck
npm run build     # dist/ に ES module、型定義、CSS
```

ライブラリを別の React アプリで使う場合は、このディレクトリをパッケージとしてインストールし、`svg-geometry-editor/style.css` を読み込みます。依存は React 18 以上だけです。ライブラリが描くのはキャンバスと選択枠だけで、ツールバー、数値パネル、undo/redo、保存は呼び出し元が持ちます。Material UI で組んだ例が `demo/` にあります。

```tsx
import { SvgGeometryCanvas } from 'svg-geometry-editor';
import type { Edits } from 'svg-geometry-editor';
import 'svg-geometry-editor/style.css';
import { useState } from 'react';

function Example({ svg }: { svg: string }) {
  const [edits, setEdits] = useState<Edits>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  return <SvgGeometryCanvas
    svg={svg} mode="edit"
    value={edits} onChange={(next, { final }) => { setEdits(next); if (final) console.log('確定', next); }}
    selectedId={selectedId} onSelectionChange={setSelectedId}
    minScale={0.2} maxScale={5}
    style={{ height: 500 }}
  />;
}
```

## 公開 API

`SvgGeometryCanvas` の props:

| prop | 型 | 説明 |
| --- | --- | --- |
| `svg` | `string` | `viewBox` を持つ SVG 文字列。画像は安全な PNG/JPEG/WebP/GIF の base64 data URL。 |
| `mode` | `'view' \| 'edit'` | 閲覧と編集を切り替える。 |
| `value`, `onChange` | `Edits`, `(value, { final }) => void` | 編集データ。必ず呼び出し元が持つ。`final` が `false` のときはドラッグ中などの途中経過、`true` のときはポインター操作の完了や矢印キーでの移動など、確定した一手。 |
| `selectedId`, `onSelectionChange` | `string \| null`, callback | 選択中の画像。必ず呼び出し元が持つ。 |
| `minScale`, `maxScale` | `number` | ハンドルとピンチで変えられる倍率の範囲。既定は 0.1〜10。 |
| `moveStep` | `number` | 矢印キー 1 回の移動量。既定は 1。 |
| `onViewChange` | `(view) => void` | 表示ズーム・パンが変わったときの通知。倍率表示などに使う。 |
| `onError` | `(notice) => void` | SVG 不正、除去内容、編集データの不一致を通知。 |
| `className`, `style` | React 標準 | キャンバス要素。高さを指定する。 |

`ref` で `zoom(factor)` と `resetView()` を呼べます。数値パネルなどの外部の操作向けに、行列を受けて行列を返す `setPosition`、`translateBy`、`scaleTo`、`rotateBy` と、行列を位置・倍率・角度に分ける `decompose` を公開しています。倍率と回転は画像の移動量（行列の平行移動成分）を中心に変えます。

見た目は CSS 変数で変えられます。`--sge-accent`（選択枠とハンドルの線）、`--sge-selection-fill`、`--sge-handle-fill`、`--sge-error` を `.sge-canvas` または `className` で渡したクラスに設定します。

編集ロジックは `geometry.ts`、`gesture.ts`、`edits.ts`、`sanitize.ts`、`useCanvasController.ts` に分かれ、`SvgGeometryCanvas.tsx` が SVG とオーバーレイを描画します。

## 操作

- **閲覧:** ドラッグ／1本指で画面移動、ホイール位置を中心にズーム、2本指でズームと移動。
- **編集:** 画像をクリック／タップして選択し、ドラッグで移動。四隅の丸いハンドルで縦横比を保って拡大縮小、上の丸で回転。選択画像上の2本指で移動と拡大縮小。画像のない所をドラッグすると画面移動、タップすると選択解除。ホイールと2本指でのズームは閲覧と同じ。
- キャンバスにフォーカスした PC では矢印キーで移動、Shift+矢印で10倍移動、Escapeで進行中の操作を取り消す。
- 表示ズームとパンは編集データに含まれない。
- デモではさらに、ツールバーの拡大・縮小・全体表示、確定単位の戻す／やり直す、数値パネル（X、Y、倍率、角度と微調整ボタン、画像を初期状態に戻す）を組み込んでいる。
- 途中で指が増えたとき、画像ドラッグ中に同じ画像へ2本目を置いた場合だけ画像ピンチへ移る。それ以外の増減では進行中の操作を確定して残りの指を離すまで待つ。`pointercancel`、ウィンドウのフォーカス喪失では進行中の操作を元に戻す。

デモのモバイル表示ではツールバーが下に移り、「詳細」から数値パネルを開けます。キャンバスに `touch-action: none` を設定し、ページスクロールとの競合を避けます。キャンバス外は通常どおりスクロールできます。ボタンとハンドルのタッチ領域は約44 CSS pxです。`safe-area-inset-bottom` と `dvh` をデモで使用しています。

## 編集データと座標系

```json
{ "photo": [1, 0, 0, 1, 12, -5] }
```

キーは画像の `id`、値は行列 `[a,b,c,d,e,f]` で、SVG の `matrix(a b c d e f)` と同じ順です。編集していない画像はキーを持ちません。行列は**各画像の親要素の SVG ユーザー座標系**で定義し、画像の直前に挿入するラッパー `<g>` に適用します。画像自身の既存 `transform` は内側に保持され、最終変換は `親の既存変換 × 保存行列 × 画像の既存変換` です。保存行列に既存transform、親transform、viewBox、表示ズーム・パンは含まれません。

どの SVG テンプレートの編集かは、保存する側が包んで持ちます。デモでは次の形で保存しています。テンプレートの構造や画像の初期値を変えたら revision を更新し、読み込み時に照合してください。

```json
{ "templateId": "card-42", "templateRevision": "rev-3", "images": { "photo": [1, 0, 0, 1, 12, -5] } }
```

コンポーネント内部ではサーバー通信をしません。保存例:

```ts
async function saveEdits(edits: Edits) {
  const response = await fetch('/api/edits', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ templateId: 'card-42', templateRevision: 'rev-3', images: edits })
  });
  if (!response.ok) throw new Error('保存に失敗しました');
}
```

デモの「保存 API 例」ボタンも同じ `fetch` を実行します。デモにはバックエンドがないため、実際の保存には呼び出し元の API が必要です。

## SVG の取り込みと制限

SVG は `DOMParser` で一度だけ解析し、許可した SVG 要素・属性だけで新しい DOM を作ってから表示します。`script`、`foreignObject`、イベント属性、`style`、DTD、外部 URL、危険な `url()` は除去または拒否し、`onError` で知らせます。SVG 内の ID と `#id` 参照はインスタンスごとに付け替えます。安全な base64 ラスター画像、基本図形、テキスト、入れ子の `g`、既存transform、`clipPath`、`mask`、グラデーションを対象とします。

編集できるのは一意で安定した `id` と安全な `href` を持つ `<image>` だけです。外部画像URLは読み込みません。画像データは呼び出し元で SVG に埋め込んでください。`clipPath` と `mask` は固定の祖先グループに置く構造を推奨します。画像自身に付いたクリップやマスク、CSS style、フィルター、アニメーション、外部 `<use>`、任意形状フレームへの自動フィットは対象外です。複数選択、パス編集、画素加工も行いません。非常に複雑な SVG は除去通知を確認し、必要な表現だけに簡略化してください。

`onError` は取り込み時の除去内容も通知します。編集データは、編集できる画像の ID であることと、有限で逆行列を持つ 6 要素の行列であることを検証し、合わない項目は無視して通知します。

## 検証

`npm test` は行列の逆変換・ズーム基点・位置/倍率/角度の個別変更、編集データの再読込、SVGサニタイズ、指の残数処理、デモの履歴を確認します。Chrome デモで画像選択、ドラッグ、倍率変更、undo/redo、ズーム後の選択表示、モバイル幅のレイアウトを確認しました。実機タッチ、画面回転、ソフトキーボードの操作は未確認です。
