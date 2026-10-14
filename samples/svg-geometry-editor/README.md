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

ライブラリを別の React アプリで使う場合は、このディレクトリをパッケージとしてインストールし、`svg-geometry-editor/style.css` を読み込みます。React 18 以上、Material UI と Emotion は peer dependency です。コンポーネントは呼び出し元の Material UI テーマを使用します。

```tsx
import { SvgGeometryEditor, emptyDocument } from 'svg-geometry-editor';
import 'svg-geometry-editor/style.css';
import { useState } from 'react';

function Example({ svg }: { svg: string }) {
  const [value, setValue] = useState(emptyDocument('card-42', 'rev-3'));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  return <SvgGeometryEditor
    svg={svg} templateId="card-42" templateRevision="rev-3" mode="edit"
    value={value} selectedId={selectedId}
    onSelectionChange={setSelectedId}
    onChange={setValue} // ドラッグ中や数値入力中のプレビュー
    onCommit={document => console.log('確定', document)}
    onError={notice => console.warn(notice.code, notice.message)}
    constraints={{ minScale: 0.2, maxScale: 5, moveStep: 1, rotationStep: 1 }}
    style={{ height: 500 }}
  />;
}
```

## 公開 API

| prop | 型 | 説明 |
| --- | --- | --- |
| `svg` | `string` | `viewBox` を持つ SVG 文字列。画像は安全な PNG/JPEG/WebP/GIF の base64 data URL。 |
| `templateId`, `templateRevision` | `string` | 保存データの一致検証に使うテンプレート識別子。 |
| `mode` | `'view' \| 'edit'` | 閲覧と編集を明確に切り替える。 |
| `value`, `defaultValue` | `EditorDocument` | 編集データ。`value` は外部制御、`defaultValue` は初期値。 |
| `selectedId`, `onSelectionChange` | `string \| null`, callback | 選択画像の制御と通知。 |
| `constraints` | `EditorConstraints` | 最小・最大倍率、移動・回転の微調整幅。 |
| `onChange` | `(document) => void` | 操作中の変化を通知。 |
| `onCommit` | `(document) => void` | ポインター操作完了、数値入力のフォーカス喪失、ボタン操作、undo/redo 時の確定通知。 |
| `onError` | `(notice) => void` | SVG 不正、除去内容、保存データ不一致を通知。 |
| `className`, `style` | React 標準 | 外側コンテナ。高さを指定する。 |

編集ロジックは `geometry.ts`、`gesture.ts`、`document.ts`、`sanitize.ts`、`useEditorController.ts` に分離し、Material UI は `EditorControls.tsx` だけで使用します。`SvgCanvas.tsx` は SVG DOM とオーバーレイを描画します。

## 操作

- **閲覧:** ドラッグ／1本指で画面移動、ホイール位置を中心にズーム、2本指でズームと移動。ボタンで拡大・縮小・全体表示。
- **編集:** 画像をクリック／タップして選択し、ドラッグで移動。四隅の丸いハンドルで縦横比を保って拡大縮小、上の丸で回転。選択画像上の2本指で移動と拡大縮小。画面移動は「画面移動」ツールを選択する。
- 数値パネルで X、Y、倍率、角度を編集。移動・倍率・回転のボタンでも微調整できる。キャンバスにフォーカスした PC では矢印キーで移動、Shift+矢印で10倍移動、Escapeで進行中の操作を取り消す。
- 戻す／やり直すは確定操作単位。画像を初期状態に戻すボタンは選択画像の編集行列だけを削除する。表示ズームとパンは編集履歴にも保存JSONにも含まれない。
- 途中で指が増えたとき、画像ドラッグ中に同じ画像へ2本目を置いた場合だけ画像ピンチへ移る。それ以外の増減では進行中の操作を確定して残りの指を離すまで待つ。`pointercancel`、ウィンドウのフォーカス喪失では進行中の操作を元に戻す。

モバイルではツールバーが下に移り、「詳細」から数値パネルを開けます。キャンバスに `touch-action: none` を設定し、ページスクロールとの競合を避けます。キャンバス外は通常どおりスクロールできます。ボタンとハンドルのタッチ領域は約44 CSS pxです。`safe-area-inset-bottom` と `dvh` をデモで使用しています。

## 保存形式と座標系

```json
{
  "schemaVersion": 1,
  "templateId": "card-42",
  "templateRevision": "rev-3",
  "images": [
    { "elementId": "photo", "matrix": [1, 0, 0, 1, 12, -5] }
  ]
}
```

行列は `[a,b,c,d,e,f]`、SVG の `matrix(a b c d e f)` と同じ順です。**各画像の親要素の SVG ユーザー座標系**で定義し、画像の直前に挿入するラッパー `<g>` に適用します。画像自身の既存 `transform` は内側に保持され、最終変換は `親の既存変換 × 保存行列 × 画像の既存変換` です。保存行列に既存transform、親transform、viewBox、表示ズーム・パンは含まれません。同一 `templateId` と `templateRevision` の SVG に JSON を再適用すると同じ配置になります。テンプレートの構造や画像の初期値を変えたら revision を更新してください。

コンポーネント内部ではサーバー通信をしません。保存例:

```ts
async function saveEdits(document: EditorDocument) {
  const response = await fetch('/api/edits', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(document)
  });
  if (!response.ok) throw new Error('保存に失敗しました');
}
```

デモの「保存 API 例」ボタンも同じ `fetch` を実行します。デモにはバックエンドがないため、実際の保存には呼び出し元の API が必要です。

## SVG の取り込みと制限

SVG は `DOMParser` で一度だけ解析し、許可した SVG 要素・属性だけで新しい DOM を作ってから表示します。`script`、`foreignObject`、イベント属性、`style`、DTD、外部 URL、危険な `url()` は除去または拒否し、`onError` で知らせます。SVG 内の ID と `#id` 参照はインスタンスごとに付け替えます。安全な base64 ラスター画像、基本図形、テキスト、入れ子の `g`、既存transform、`clipPath`、`mask`、グラデーションを対象とします。

編集できるのは一意で安定した `id` と安全な `href` を持つ `<image>` だけです。外部画像URLは読み込みません。画像データは呼び出し元で SVG に埋め込んでください。`clipPath` と `mask` は固定の祖先グループに置く構造を推奨します。画像自身に付いたクリップやマスク、CSS style、フィルター、アニメーション、外部 `<use>`、任意形状フレームへの自動フィットは対象外です。複数選択、パス編集、画素加工も行いません。非常に複雑な SVG は除去通知を確認し、必要な表現だけに簡略化してください。

`onError` は取り込み時の除去内容も通知します。保存データは schema、テンプレート識別子、画像 ID、有限な6要素行列を検証します。編集可能画像以外のデータを渡した場合は無視し、通知します。

## 検証

`npm test` は行列の逆変換・ズーム基点、履歴、保存JSONの再読込、SVGサニタイズ、指の残数処理を確認します。Chrome デモで画像選択、ドラッグ、倍率変更、undo/redo、ズーム後の選択表示、モバイル幅のレイアウトを確認しました。実機タッチ、画面回転、ソフトキーボードの操作は未確認です。
