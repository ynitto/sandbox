# SVG クリップ画像エディタ 設計

SVG に含まれる「クリップされた画像」の見え方（どの部分がクリップ枠に収まるか）を、
マウスまたはタッチで画像を動かして合わせ込む React コンポーネントの設計。
出力は、元の SVG と同じ座標系のまま画像の座標だけを書き換えた SVG。

> 状態: 設計（未実装）。画面のイメージ（§7）の承認を経てから実装に入る。
> 実装の置き場所は承認時に決める（案: `tools/svg-clip-image-editor/`）。
> 初版は 1 エディタ = 1 画像。複数画像の切り替えは §8 で形だけ決め、モデルと API をそれに合わせておく。
> 契約を検査する CLI（§2.8）は設計だけで、実装しない。

## 1. 要件と方針

| 要件 | 決めたこと |
|---|---|
| クリップされた画像の、枠に入る領域を更新する | 変えるのは対象 `<image>` の `transform` 属性**だけ**。`x` / `y` / `width` / `height` / `href` / クリップ形状 / 他の要素には触れない |
| クリップ枠は固定、画像を拡大縮小・回転・平行移動する | 編集は「クリップ空間（§3）で画像に左から掛ける相似変換 Δ」に一本化する。スキューは作らない（元の `transform` にあるスキューは保つ） |
| 編集 UI はクリップ枠の近辺に注目して表示する | エディタの `viewBox` を「クリップ枠のルート座標での外接矩形＋余白」に固定する |
| 注目領域は拡大縮小・回転・平行移動できない | `viewBox` は画像を選んだときに 1 回だけ決め、操作で変えない。ブラウザ自体のピンチズーム・スクロールも止める（§5.5） |
| マウス・タッチで画像を合わせ込む | Pointer Events に統一。1 本指/左ドラッグ = 移動、2 本指 = 拡縮＋回転＋移動、ホイール = 拡縮（Shift で回転） |
| 枠内は透明（画像が不透明に見える）、枠外は半透過で画像全体が透ける | 画像を 2 枚重ねる。下は全体を `opacity` で半透過、上はクリップ枠で切り抜いた不透明の画像（§5.2） |
| 出力は元と同じ座標系 | ルートの `viewBox` / `width` / `height` と、対象画像より上の要素の `transform` は変えない。画像の `transform` を `matrix(...)` で書き直すだけ |
| 枠の内側に画像のない所を作らない（覆い制約） | 枠の形の**凸包**だけで判定する。凹んだ形・穴・曲線・複数の輪郭でも、正確に（許容誤差 ε 以内で）解ける（§6.4） |
| 枠の端に当たったとき | ゴムのように抵抗しながら端を越えて動き、離すとばねで戻る。弾いたときは慣性で滑り、端に当たると同じく戻る（§6.5） |
| 画像と関係ない飾りが増えても編集できる | 編集できる画像を**スロット**という閉じた部品として SVG の契約で定める（§2）。スロットの外は自由。契約は機械で検査する（§2.6） |
| 将来、1 つのエディタで複数の画像を切り替える | 切り替えの入口（一覧・タブなど）はコンポーネントの外が持つ。コンポーネントは `slot` プロップで指定されたスロットを編集するだけ。モデル・状態・履歴・API を最初からスロット名をキーにした形にしておく（§8） |

採らなかったもの:

- **クリップ形状を逆向きに動かす実装**（画像を固定して枠を動かす）: 出力でクリップを書き換えることになり、
  共有された `<clipPath>` を壊す。
- **`<canvas>` で描いて合成する実装**: 外部 URL の画像で CORS の制約を受け、SVG のベクタ性も失う。
  SVG のまま描けば同じ描画エンジンで出力と寸分違わず見える。
- **元の SVG 文字列を DOM にそのまま差し込む実装**: スクリプトやイベント属性を実行しうる。
  エディタは必要な要素だけを組み直して描く（§9）。
- **対象画像を構造から推測する**（「クリップのかかった最初の `<image>`」など）: 飾りにクリップ付きの画像が
  1 つ増えただけで対象がずれる。対象は明示の印（`data-clip-slot`）でだけ決める。

## 2. SVG の契約

### 2.1 全体像

編集できる画像は**スロット**として印を付ける。スロットは「クリップを持つ `<g>` と、その唯一の子の `<image>`」の組で、
中身を閉じた部品として扱う。スロットの外（飾り・文字・図形・他の画像）は、§2.5 の数項目を除いて何を置いてもよい。

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="vx vy vw vh" width="…" height="…">
  <defs>
    <clipPath id="hero-clip" clipPathUnits="userSpaceOnUse" [transform="Q"]>
      <!-- rect / circle / ellipse / polygon / polyline / path の 1 つ以上（和集合） -->
    </clipPath>
    <!-- 飾り用のグラデーション・フィルタ・他のクリップなどは自由 -->
  </defs>

  <!-- 飾り（自由） -->

  <g [transform="…"]>                               <!-- 祖先: <g> だけ、任意段数。transform は K に積まれる -->
    <g data-clip-slot="hero"                        <!-- スロット: 名前は文書内で一意 -->
       clip-path="url(#hero-clip)" [transform="…"]>
      <image href="…" x="…" y="…" width="…" height="…"
             preserveAspectRatio="…"
             [transform="M"]/>                      <!-- 唯一の子。書き換えるのは transform だけ -->
    </g>
  </g>

  <!-- 飾り（自由。スロットより後ろの要素はスロットの上に描かれる） -->
</svg>
```

クリップを持つ要素（スロットの `<g>`）と動かす要素（`<image>`）を分けるのが要点。
`clipPathUnits="userSpaceOnUse"` の座標は「クリップを参照する要素のユーザー空間」で解釈され、
その要素自身の `transform` も含む。`<image clip-path="…" transform="…">` のように同じ要素に両方があると、
画像の `transform` を変えた瞬間に枠も一緒に動いてしまう。

### 2.2 スロットの規則

| 番号 | 規則 | 理由 |
|---|---|---|
| S1 | スロットは `data-clip-slot="名前"` を持つ `<g>`。名前は `[a-z0-9][a-z0-9-]*` で文書内で一意 | 対象を推測しない。複数画像（§8）でもキーになる |
| S2 | スロットの `<g>` は `clip-path="url(#id)"` を**属性で** 1 つ持つ | `style` や CSS クラス経由のクリップは解析だけでは決まらない |
| S3 | スロットの子は `<image>` ちょうど 1 つ（空白とコメントは除く）。間に `<g>` を挟まない | 動かす対象と座標の対応を 1 通りにする。スロットの中に飾りを置くと、画像と一緒には動かず枠には切られる、という分かりにくい挙動になる |
| S4 | `<image>` は `x` / `y` / `width` / `height` を単位なしの数値で持つ。`clip-path` / `mask` / `style` の transform 系を持たない | 描画矩形を解析だけで決めるため |
| S5 | `<image>` の `href`（または `xlink:href`）は `data:image/*` / `https:` / `http:` / `blob:` / 相対 URL | §9 |
| S6 | スロットの `<g>` は `mask` を持たない。`opacity` と `filter` は持ってよいが、編集画面では再現しない（警告） | マスクは枠の見え方を変え、覆い制約の前提が崩れる |

### 2.3 クリップパスの規則

| 番号 | 規則 | 理由 |
|---|---|---|
| C1 | 参照先の `<clipPath>` が文書内にあり、`clipPathUnits` は `userSpaceOnUse`（省略も可） | `objectBoundingBox` だと枠が画像の外接矩形に追従して動く |
| C2 | 子は `rect` / `circle` / `ellipse` / `polygon` / `polyline` / `path` だけ。各子の `transform` は可。`clip-rule` は自由 | 凸包（§6.4）を解析だけで求められる形に限る。`<text>` と `<use>` を除く |
| C3 | `<clipPath>` にも子にも `clip-path` を付けない（クリップの入れ子なし） | 枠の形を 1 段で決める |
| C4 | 同じ `<clipPath>` を複数のスロットや飾りが共有してよい | エディタはクリップを書き換えない |

### 2.4 祖先の規則

| 番号 | 規則 | 理由 |
|---|---|---|
| A1 | ルートからスロットまでの祖先は `<g>` だけ（`<svg>` の入れ子・`<symbol>` / `<use>` / `<switch>` / `<a>` / `<foreignObject>` を挟まない） | ビューポート変換や条件分岐が入ると K（§3）が解析だけで決まらない |
| A2 | 祖先の `transform` は属性で書く。逆行列を持つこと | 同上 |
| A3 | 祖先は `clip-path` / `mask` を持たない | 外側で切られると、エディタの枠と実際に見える範囲がずれる |
| A4 | 祖先の `opacity` / `filter` は可（編集画面では再現しない） | 見え方だけで座標には効かない |

### 2.5 文書全体の規則

| 番号 | 規則 | 理由 |
|---|---|---|
| D1 | ルートの `<svg>` は `viewBox` を持つ | 出力の座標系を明示する |
| D2 | `id` は文書内で一意 | クリップの参照を 1 通りにする |
| D3 | `<use>` がスロットとその子孫を参照しない | 参照先の画像が編集で一緒に動き、予期しない所が変わる |
| D4 | CSS（`<style>` と `style` 属性）が、スロット・その `<image>`・祖先に `transform` / `translate` / `rotate` / `scale` / `transform-origin` / `transform-box` / `clip-path` / `mask` を当てない | CSS の変形は属性の `transform` を上書きし、解析した座標とずれる |

D4 は CSS の規則ごとに、セレクタがスロット・画像・祖先のどれかに一致するかを `element.matches(selectorText)` で確かめる
（`<style>` の本文は `CSSStyleSheet.replaceSync()` で規則に分ける）。飾りに CSS の変形やアニメーションを使うのは自由。

**ここに挙げていないものはすべて自由**: 飾りの要素の種類・数・順序、テキスト、グラデーション、フィルタ、
アニメーション、別のクリップパス、印のない（＝編集しない）クリップ付き画像、スクリプト（エディタは実行しない）。

### 2.6 検査（lint）

`lintClipDocument(svg): LintIssue[]` が §2.2〜§2.5 を検査する。読み込み（§4）は最初にこれを呼び、
エラーが 1 つでもあれば編集を始めない。同じ関数を CLI（§2.8、設計のみ）からも呼べる形にしておく。

```ts
interface LintIssue {
  rule: string;              // 'S3' など §2 の番号
  severity: 'error' | 'warning';
  slot?: string;             // 関係するスロット名
  path: number[];            // 違反した要素への子インデックス列
  location: string;          // 人が読む位置: '/svg/g[2]/g[1]'（同名の兄弟の中での 1 始まりの番号）
  message: string;
}
```

警告（編集はできる）: S6 / A4 の `opacity` / `filter`、文脈表示（§5.3）で読めない外部 URL の画像。

lint の本体は DOM の `Document` と、CSS を規則に分ける関数を受け取る形にし、実行環境に依存させない。

```ts
type CssRule = { selector: string; properties: string[] };
lintDocument(doc: Document, parseCss: (text: string) => CssRule[]): LintIssue[];
lintClipDocument(svg: string): LintIssue[];   // ブラウザ用: DOMParser と CSSStyleSheet.replaceSync で上を呼ぶ
```

### 2.7 既存の SVG の取り込み（任意）

契約に合わない既存の SVG は、`adoptClipDocument(svg, { image: 'photo' })` で**描画結果を変えずに**契約の形へ
書き換えてから使う。読み込み時に暗黙には行わない（どこを書き換えたかを利用者が確かめられるように、別の関数にする）。

| 入力 | 書き換え |
|---|---|
| `<image>` 自身が `clip-path` を持つ | `<g data-clip-slot clip-path="…" transform="元の transform">` で包み、`<image>` から `clip-path` を外して `transform` を恒等にする |
| `<image>` と `clip-path` の `<g>` の間に `<g transform="J">` がある | 中間の `<g>` を外し、`<image transform>` を `J·M` にする（中間の `<g>` が他の子を持つときは取り込めない） |
| `clipPathUnits="objectBoundingBox"` | 読み込み時点の外接矩形 `(bx, by, bw, bh)` で `userSpaceOnUse` に焼き直した**複製**の `<clipPath>`（新しい id、`transform="matrix(bw 0 0 bh bx by)"` を Q の前に積む）を作り、参照先を差し替える。元の `<clipPath>` は他の参照があり得るので残す |
| ルートに `viewBox` がない | `0 0 width height` を足す（`width` / `height` が px か単位なしのときだけ） |
| スロットの印がない | 指定した `<image>` を含む `<g>` に `data-clip-slot`（既定名 `image-1`, `image-2` …）を付ける |

### 2.8 契約検査 CLI（設計のみ、実装しない）

テンプレートの SVG をリポジトリで管理する場合に、契約違反を作った時点で CI で落とすための CLI。
ここでは形だけを決め、実装はしない。実装するときは §2.6 の `lintDocument` をそのまま呼ぶ薄い入口にし、
規則の判定を CLI 側に書かない（ブラウザと CLI で結果が分かれないように）。

```
svg-clip-lint [options] <file|glob>...

  --format text|json      出力の形式（既定 text）
  --strict                警告もエラーとして扱う
  --require-slot <name>   指定した名前のスロットがなければエラー（複数回指定できる）
  --quiet                 エラーだけを出す
```

| 項目 | 決めたこと |
|---|---|
| 実行環境 | Node。`Document` は jsdom で作り、CSS は css-tree で規則に分けて `lintDocument` に渡す（jsdom は `CSSStyleSheet.replaceSync` を持たないため） |
| text の出力 | 1 違反 1 行: `<file>:<location> <severity> <rule> <message>`。例: `card.svg:/svg/g[2] error S3 スロット hero の子が <image> 1 つではない（<rect> がある）` |
| json の出力 | `[{ file, issues: LintIssue[] }]` |
| 終了コード | 0 = エラーなし（警告だけは 0）、1 = エラーあり（`--strict` なら警告も）、2 = 引数の誤り・読めないファイル・XML として読めない |
| 対象 | 引数のファイルと glob。ディレクトリは `**/*.svg` として辿る |
| しないこと | 自動修正（`adoptClipDocument` 相当の書き換え）は持たない。取り込みは人が結果を確かめる操作なので、CLI で一括にしない |

## 3. 座標系

行列は SVG の `matrix(a b c d e f)` の 6 要素で表し、点 `p` への適用を `X·p` と書く。

| 記号 | 空間 | 定義 |
|---|---|---|
| R | ルート | ルート `<svg>` の `viewBox` 座標。出力の座標系そのもの |
| C | クリップ空間 | スロットの `<g>` のユーザー空間。クリップ形状はここで定義される |
| I | 画像ローカル | `<image>` の `x` / `y` / `width` / `height` が置かれる空間 |
| P | クリップ形状 | `<clipPath>` の子の座標 |
| S | スクリーン | ブラウザの CSS ピクセル（ポインタの `clientX` / `clientY`） |

```
p_R = K · p_C          K = ルート直下からスロットの <g> 自身までの transform の積
p_C = M · p_I          M = <image> の transform（契約 S3 により間に何も挟まらない）
p_C = Q · T_k · p_P    Q = <clipPath> の transform、T_k = k 番目の子の transform
p_S = V · p_R          V = エディタの <svg> の getScreenCTM()
```

`K` / `Q` / `T_k` / `M` は `transform` 属性の文字列を自前で解析して求める
（`matrix` / `translate` / `scale` / `rotate(θ [cx cy])` / `skewX` / `skewY`）。
`getCTM()` に頼らないのは、元の SVG を DOM に載せずに Node だけで単体テストできるようにするため。
契約（§2.4 A1・A2、§2.5 D4）により、属性だけで座標が決まることが保証される。

### 3.1 編集の表し方

編集は C 空間で左から掛ける変換 Δ で表す（枠が C 空間で固定なので、操作の中心や移動量を枠と同じ空間で扱える）。

```
M_new = Δ · M_start
```

ジェスチャ中は毎回「ジェスチャ開始時の `M_start`」から計算し直す（差分を積み重ねると誤差が溜まる）。

### 3.2 ポインタ位置の変換

```
p_C = K⁻¹ · V⁻¹ · p_S
```

`V` はポインタイベントのたびに `svg.getScreenCTM()` から取り直す（スクロールやレイアウト変更に追従するため。軽い呼び出し）。

## 4. 処理の構成

純粋関数（DOM の描画に依存しない）と React 部分を分ける。純粋関数だけで出力が決まるので、単体テストで出力を固定できる。

```
svg 文字列
   │ lintClipDocument()             契約の検査（§2.6）。エラーがあればここで止める
   │ parseClipDocument()            DOMParser で解析 → スロットごとに K/M/クリップ形状/凸包
   ▼
ClipDocModel（slots[]）──────────────┐
   │                                 │ serialize(model, transforms)  元の Document を複製し各 <image transform> を差し替え
   ▼                                 ▼
<SvgClipImageEditor>               出力 svg 文字列
   ├ useFocusViewBox(slot)          注目領域（固定の viewBox）
   ├ useImageGesture()              Pointer / Wheel / Key → Δ → M
   ├ useElastic()                   覆い制約のゴム・慣性・ばね（§6.5）
   └ useEditorState()               スロットごとの M・作業中のスロット・履歴
```

| モジュール | 責務 | 主な関数 |
|---|---|---|
| `matrix.ts` | 2D アフィン行列 | `multiply` `invert` `apply` `translate` `scale` `rotate` `parseTransform` `formatMatrix` `decompose` |
| `lint.ts` | 契約の検査 | `lintDocument(doc, parseCss)` `lintClipDocument(svg)` |
| `adopt.ts` | 既存 SVG の取り込み | `adoptClipDocument(svg, opts): { svg, changes }` |
| `parse.ts` | 解析 | `parseClipDocument(svg): Result<ClipDocModel>` |
| `path.ts` | パスの解析と区分化 | `parsePathData(d)` → 直線と 3 次ベジェだけの列（2 次は次数上げ、円弧はベジェへ） |
| `geometry.ts` | 枠の外接矩形・凸包、画像の描画矩形、注目領域 | `clipHull(shapes, Q, ε)` `drawnImageRect(image, natural?)` `focusRect(slot, padding)` |
| `gesture.ts` | ポインタ列 → Δ | `panDelta` `pinchDelta` `wheelDelta` |
| `constraint.ts` | 覆い制約・拡大率の上下限 | `isCovered(slot, M)` `coverCorrection(slot, M)` `clampScale(M, M0, min, max)` |
| `elastic.ts` | ゴムの抵抗・慣性・ばね | `rubber(d, L)` `rubberInverse(r, L)` `elasticView(slot, Mraw)` `stepMomentum(state, dt)` `stepSpring(state, dt)` |
| `context.ts` | 文脈表示の上下の層 | `splitContext(model, slot, transforms): { below, above }` |
| `serialize.ts` | 出力 | `serialize(model, transforms, opts): string` |
| `SvgClipImageEditor.tsx` | 描画と操作 | — |

### 4.1 モデル

```ts
type Matrix = [a: number, b: number, c: number, d: number, e: number, f: number];
type Point = [x: number, y: number];
type Rect = { x: number; y: number; width: number; height: number };

interface ClipDocModel {
  doc: Document;                 // 元文書（出力の土台。描画には使わない）
  rootViewBox: Rect;             // R
  slots: ClipSlot[];             // 文書順。初版はこのうち 1 つを編集する
  warnings: LintIssue[];
}

interface ClipSlot {
  name: string;                  // data-clip-slot
  path: number[];                // doc 内のスロット <g> への子インデックス列
  K: Matrix;                     // C → R
  M0: Matrix;                    // 読み込み時の <image> の transform
  image: {                       // 描画用に組み直した属性（許可リストのみ）
    href: string; hrefAttr: 'href' | 'xlink:href';
    x: number; y: number; width: number; height: number;
    preserveAspectRatio: string;
  };
  clip: {
    id: string;
    Q: Matrix;
    shapes: ClipShape[];         // { tag, attrs: 幾何属性のみ, transform }
    bboxC: Rect;                 // C 空間での外接矩形
    hullC: Point[];              // C 空間での凸包（反時計回り。§6.4）
  };
}
```

### 4.2 注目領域（固定の viewBox）

1. 枠の凸包 `hullC` の頂点を `K` で R に写し、その外接矩形 `F` を取る（スロットや祖先が回転していても枠全体が入る）。
2. `F` の各辺に `focusPadding`（既定 0.25 = 幅・高さの 25%）の余白を足す。
3. それをエディタの `viewBox` にして `preserveAspectRatio="xMidYMid meet"` で表示する。

向きはルート座標のまま（出力と同じ向き）。`viewBox` は作業中のスロットが決まったときだけ計算し、操作では変えない。
エディタの表示サイズが変わっても `viewBox` は同じなので、注目領域は動かない。

## 5. 描画

### 5.1 エディタ内の SVG

```tsx
<svg ref={svgRef} viewBox={focus} preserveAspectRatio="xMidYMid meet"
     style={{ touchAction: 'none', userSelect: 'none' }}
     role="application" aria-label="画像の位置を調整" tabIndex={0}>
  <defs>
    <clipPath id={`${uid}-clip`} clipPathUnits="userSpaceOnUse" transform={Q}>
      {clip.shapes /* 許可リストで組み直した形状 */}
    </clipPath>
  </defs>

  {/* 任意: 文脈の下の層（§5.3） */}

  <g transform={K}>
    {/* ① 画像全体（半透過）: 枠の外にはみ出した部分が透けて見える */}
    <image {...image} transform={M} opacity={outsideOpacity} />
    {/* ② 枠で切り抜いた画像（不透明） */}
    <g clipPath={`url(#${uid}-clip)`}>
      <image {...image} transform={M} />
    </g>
  </g>

  {/* 任意: 文脈の上の層（§5.3） */}

  {/* ③ 枠の輪郭（太さは画面上で一定）。覆い制約を満たさない間は警告色 */}
  <g transform={K}>
    <g transform={Q} fill="none" pointerEvents="none">
      {clip.shapes.map(s => <Shape {...s} strokeDasharray="6 4" vectorEffect="non-scaling-stroke"
                                  stroke={covered ? 'var(--clip-editor-frame)' : 'var(--clip-editor-warn)'} />)}
    </g>
  </g>

  {/* ④ 当たり判定: viewBox 全面。画像の外を掴んでも画像が動く */}
  <rect x={focus.x} y={focus.y} width={focus.width} height={focus.height} fill="transparent" />
</svg>
```

- ② のクリップは ① と同じ `K` の中に置くので、`userSpaceOnUse` の座標は元の SVG とまったく同じ意味になる。
- `id` は `useId()` で接頭辞を付け、同じページに複数置いても衝突しない。
- 同じ `href` の `<image>` を 2 枚描くが、ブラウザはデコード結果を共有するので負荷はほぼ 1 枚分。
- 枠の輪郭は文脈の上の層より上に描き、飾りに隠れないようにする。

### 5.2 「枠内は透明、枠外は半透過」の見え方

| 領域 | 見え方 |
|---|---|
| 枠の内側 | ② が不透明で重なり、出力と同じ見え方になる（枠に覆いがない = 透明） |
| 枠の外で画像がある所 | ① だけが `outsideOpacity`（既定 0.35）で見える。画像全体の位置関係が分かる |
| 画像がない所 | 背景（市松模様、または §5.3 の文脈） |

### 5.3 文脈表示（任意、既定は出さない）

`context="layers"` のとき、作業中のスロットを除いた元の SVG を**スロットの前と後ろ**に分けて描く。
契約でスロットが閉じた部品になっているので、文書順で前後に切り分けられる。

`splitContext(model, slot, transforms)`:

1. 文書を 2 つ複製する。どちらでも作業中のスロットを取り除く。
2. **下の層**: 文書順（前順）でスロットより後ろにある要素を、スロットの祖先を除いて取り除く。
3. **上の層**: スロットより前にある要素を、祖先を除いて取り除く。
4. どちらでも資源の要素（`<defs>` とその中身、`<clipPath>` / `<mask>` / `<pattern>` / グラデーション / `<filter>` / `<marker>` / `<symbol>` / `<style>`）は残す。
5. 他のスロットの `<image transform>` は `transforms` の現在の値に差し替える（複数画像のとき、他の画像の編集結果が見える）。
6. それぞれを `data:image/svg+xml` にし、`<image x=vx y=vy width=vw height=vh preserveAspectRatio="none">` として描く。

上の層は `contextAboveOpacity`（既定 0.5）で半透過にし、飾りの下にある画像も見えるようにする。
画像として読み込んだ SVG ではスクリプトが動かず、元文書の `id` とエディタの `id` も衝突しない。

制約:

- 画像として読み込んだ SVG は外部の資源を読めない（ブラウザの仕様）。飾りが外部 URL の画像を使っていると、
  その画像は文脈に出ない（§2.6 の警告）。スロットの画像そのものは①②で直接描くので影響しない。
- 祖先の `opacity` は上下の層に別々にかかるので、重なり部分の透け方が出力とわずかに違うことがある。

### 5.4 ツールバー

エディタの下に 1 行。ボタンだけを置き、操作の説明文は常駐させない（説明は README に書く）。

| ボタン | 動き |
|---|---|
| 縮小 / 拡大 | 枠の中心を中心に ×1/1.1 / ×1.1 |
| 左に 90° / 右に 90° | 枠の中心を中心に回転 |
| 枠に合わせる | 現在の回転のまま、枠を覆う最小の拡大率にして中央に置く（§6.4） |
| 元に戻す / やり直す | 履歴（§6.6） |
| リセット | 読み込み時の `M0` に戻す |

`toolbar={false}` で隠し、ref のメソッド（§10.2）で外から同じ操作を呼べる。

### 5.5 注目領域を固定するための抑止

- エディタの `<svg>` に `touch-action: none`（ブラウザのピンチズーム・スクロールを止める）。
- `wheel` は `addEventListener('wheel', h, { passive: false })` で登録して `preventDefault()`
  （React の `onWheel` は passive なので止められない）。
- Safari の `gesturestart` / `gesturechange` も `preventDefault()`。
- `viewBox` はどの操作でも変えない。ポインタ操作は常に画像の `M` だけを変える。

## 6. 操作

### 6.1 状態遷移

```
            pointerdown(1本目)              pointerdown(2本目)
   idle ────────────────────────▶ pan ──────────────────────▶ pinch
    ▲                              │  ▲                          │
    │                              │  └──────────────────────────┘
    │                              │   pointerup(2本→1本): 残った指で pan に戻る（基準を取り直す）
    │       pointerup(最後の1本)   ▼
    │                       ┌─ 速さ ≥ 閾値（pan から離した）──▶ momentum ──┐
    │                       │                                     │ 端を越えた / 遅くなった
    │                       └─ それ以外 ─────────────────────────▶ settle ◀┘
    │                                                                │ ばねが止まった
    └─────────────────────────── commit（履歴に積む・onCommit）◀────┘

   momentum / settle 中の pointerdown → アニメーションを止め、見えている位置から pan を始める（§6.5）
```

- 3 本目以降の指は無視する。
- 本数が変わるたびに**基準を取り直す**: `M_start ← 現在の M`、開始点 ← 各ポインタの現在位置。こうすると指を足し引きしても画像が跳ねない。
- `setPointerCapture` で、指が枠や `<svg>` の外へ出ても追従する。`pointercancel` は慣性なしの `pointerup` と同じに扱う。
- `pointermove` は `requestAnimationFrame` で 1 フレーム 1 回にまとめて `M` を更新する。
- commit は画像が止まってから 1 回だけ行う。`onCommit` に渡す `M` は必ず覆い制約を満たす（`cover="elastic"` のとき）。

### 6.2 Δ の求め方（すべて C 空間）

| 操作 | Δ |
|---|---|
| 1 本指 / 左ドラッグ（開始 a₀ → 現在 a₁） | `T(a₁ − a₀)` |
| 2 本指（a₀,b₀ → a₁,b₁） | `T(a₁) · R(θ) · S(s) · T(−a₀)`、`v₀ = b₀ − a₀`、`v₁ = b₁ − a₁`、`s = |v₁| / |v₀|`、`θ = atan2(v₁) − atan2(v₀)` |
| ホイール / トラックパッドのピンチ（`ctrlKey` 付き wheel） | `T(p) · S(k) · T(−p)`、`k = exp(−deltaY · 0.0015)`、p はポインタ位置 |
| Shift + ホイール | `T(p) · R(deltaY · 0.2°) · T(−p)` |
| 矢印キー | `T(±step)`（step は注目領域の幅の 1%、Shift で 10%） |
| `+` / `-` | 枠の中心で `S(1.1)` / `S(1/1.1)` |
| `[` / `]` | 枠の中心で `R(−1°)` / `R(+1°)`（Shift で 15°） |

2 本指の Δ は「2 本の指の下にある画像の点が、指に貼り付いたまま動く」変換そのもの
（a₀ を a₁ へ、b₀ を b₁ へ正確に写す相似変換）。
「枠の中心」は凸包 `hullC` の外接矩形の中心とする。

`rotationSnap`（既定 0 = 無効、例 3）を与えると、2 本指の回転で全体の回転角が 90° の倍数 ± `rotationSnap`° に入ったとき吸着させる。

### 6.3 拡大率の上下限

全体の拡大率を `σ(M) = sqrt(|det M|)` とし、`σ(M) / σ(M0)` が `[minScale, maxScale]`（既定 `[0.1, 20]`）に
収まるよう、Δ の `s`（または `k`）を詰める。

### 6.4 覆い制約（`cover="elastic"` / `"off"`、既定 `"elastic"`）

「枠の内側に画像のない所を作らない」制約。複雑なクリップパス（凹んだ形、穴、曲線、複数の輪郭、`evenodd`）でも
次の性質で正確に扱える。

> 画像の描画矩形 `D` は I 空間で軸に沿った矩形なので、C 空間では平行四辺形（凸）になる。
> 凸な集合が図形 G を含むことと、G の**凸包** conv(G) を含むことは同値。
> したがって判定と補正には、枠の凸包の頂点だけを使えばよい。凹み・穴・塗りの規則は結果に影響しない。

枠は C 空間で固定なので、凸包はスロットを読み込んだときに 1 回だけ求めて `hullC` に持つ。

#### 凸包の求め方（`clipHull`）

1. クリップの子をすべて「直線と 3 次ベジェ」の列にする（P 空間）。
   - `rect`: 4 辺。`rx` / `ry` があれば角を 1/4 楕円の 3 次ベジェにする。
   - `circle` / `ellipse`: 1/4 ずつ 4 本の 3 次ベジェ。
   - `polygon` / `polyline`: 直線（クリップでは `polyline` も閉じた図形として塗られる）。
   - `path`: `d` を全コマンド（`M L H V C S Q T A Z`、相対・絶対）解析する。2 次ベジェは 3 次へ次数上げ、
     円弧は中心パラメータ化してから 90° 以下ずつ 3 次ベジェにする。
2. 各子の `transform`（T_k）と `Q` を、直線の端点とベジェの制御点に掛ける。アフィン変換はベジェの制御点に掛けてよい。
3. 各 3 次ベジェを、制御点と弦の距離が ε 以下になるまで二分割する（de Casteljau）。
   集める点は、直線の端点と、分割後の**制御点すべて**。ベジェ曲線は制御点の凸包の中にあるので、
   集めた点の凸包は真の凸包を必ず含み（覆い不足を起こさない）、はみ出しは ε 以下になる。
4. Andrew の monotone chain で凸包を求める（O(n log n)）。
   ε は既定で枠の外接矩形の対角線の 0.1%。

#### 判定と補正

画像の描画矩形 `D = [x₀, x₁] × [y₀, y₁]`（I 空間）は `preserveAspectRatio` で決まる:
`none` と `slice` は `x / y / width / height` の矩形そのもの、`meet` は画像の元の大きさ（読み込んで `naturalWidth` / `naturalHeight` を取る）から求めた内側の矩形。
画像の透明な画素は考えない（幾何で判定する）。

1. 凸包の頂点を I 空間に写す: `q_i = M⁻¹ · h_i`。I 空間での外接矩形を `[qx₀, qx₁] × [qy₀, qy₁]` とする。
   `D` が軸に沿った矩形なので、「全頂点が `D` に入る」は「この外接矩形が `D` に入る」と同じ。
2. 幅か高さが足りなければ拡大する: `k = max((qx₁−qx₀)/(x₁−x₀), (qy₁−qy₀)/(y₁−y₀))`。
   `k > 1` なら枠の中心 `c` を中心に `Δ = T(c)·S(k)·T(−c)` を掛け、1 を計算し直す。
   今の回転のままで覆える最小の拡大率がこの `k` になる（拡大の中心はどこでも、平行移動で調整できる）。
3. 平行移動を I 空間で求める（画像を I 空間で `u` 動かすと `q` は `−u` 動く）:
   `u_x = clamp(0, qx₁ − x₁, qx₀ − x₀)`、`u_y` も同様。`M ← M · T(u)`。
   今の位置から一番近い、覆える位置になる。

計算は凸包の頂点数 n に対して O(n)。凸包は数十〜数百点なので、毎フレーム回しても軽い。
この 2 と 3 で求めた補正（拡大 `k ≥ 1` と平行移動 `u`）を `coverCorrection(slot, M) → { k, c, u }` として返し、
§6.5 のゴムとばねはこの補正の「どこまでを見せるか」を決める。

| モード | 動き |
|---|---|
| `elastic` | 端を越えるとゴムのように抵抗し、離すとばねで覆える位置へ戻る。弾けば慣性で滑る（§6.5） |
| `off` | 制約をかけない。慣性はある。覆えていない間は枠の輪郭を警告色にするだけ |

覆い制約と拡大率の上下限（§6.3）がぶつかるときは覆い制約を優先する（覆うのに要る拡大率が `maxScale` を超えても拡大する）。

「枠に合わせる」ボタンは同じ手順で、2 の `k` を 1 未満も許し（縮めて丁度覆う）、3 の `u` を中央寄せ
（`u_x = (qx₀ + qx₁)/2 − (x₀ + x₁)/2`）にしたもの。

扱えないもの（契約で除いている）: `<text>` や `<use>` のクリップ、クリップの入れ子、`<mask>` による切り抜き。

### 6.5 ゴムの抵抗・慣性・ばね（`cover="elastic"`）

ジェスチャが動かすのは**生の行列 `M_raw`**（指に正確に追従する値）で、画面に描くのは
**見せる行列 `M_view = elasticView(slot, M_raw)`**。端の内側では両者は等しく、端を越えた分だけ
`M_view` が `M_raw` より手前に留まる。これが指に対して画像が遅れてついてくる「抵抗」になる。

#### 抵抗（端を越えている間）

`coverCorrection(slot, M_raw)` の補正を、越えた量に応じて一部だけ打ち消す。

- ゴムの関数（越えた量 d → 見せる越え量）: `rubber(d, L) = L · (1 − 1 / (d·0.55/L + 1))`。
  d が小さいうちはほぼ d、大きくなるほど L に近づいて止まる。逆関数は `rubberInverse(r, L) = (L/0.55) · (1/(1 − r/L) − 1)`。
- **平行移動**: 越え量は I 空間のベクトル `−u`。`L` は枠の外接矩形の短辺を I 空間に写した長さの 1/4。
  見せる補正は `u_view = u · (1 − rubber(|u|, L)/|u|)`。
- **拡大率**（縮めすぎ）: 越え量を対数で測る（`s = ln k`）。`L = ln 1.5`。
  見せる補正は `k_view = exp(s − rubber(s, L))` を枠の中心で掛ける。
- 順序は §6.4 と同じく、拡大率を先、平行移動を後に計算する。
- 回転で覆えなくなった場合も、その分は拡大率と平行移動の越えとして同じ式で抵抗になる（回転そのものは止めない）。

#### 慣性（pan から速く離したとき）

- 直近 100ms のポインタ位置（C 空間）から速度 `v` を求める。`|v|` が注目領域の幅 0.3 倍/秒 以上なら momentum に入る。
  2 本指から離したとき・ホイール・キー・ボタンでは慣性を付けない。
- momentum 中は毎フレーム `M_raw ← T(v·dt) · M_raw`、`v ← v · exp(−dt/0.325s)`（摩擦）。
  描くのは常に `M_view = elasticView(M_raw)` なので、端を越えた分はゴムで縮んで見える。
- 次のどちらかで settle へ移る: 端を越えていて `v` が外向き（越え量を増やす向き）になった、または `|v|` が閾値の 1/10 を下回った。
  端を越えて settle へ移るときは、そのときの速度をばねの初速として引き継ぐ（端に当たって跳ね返る見え方になる）。

#### ばね（settle）

- 目標は覆える位置 `M* = M_view に coverCorrection を全部かけたもの`。
- 平行移動（I 空間の 2 次元）と拡大率（対数）を、それぞれ臨界減衰のばねで目標へ寄せる:
  `x(t) = (x₀ + (v₀ + ω·x₀)·t) · e^(−ω t)`、`ω = 2π / 0.4s`。x は目標からのずれ。
- ずれが 0.5 画面 px 未満かつ速さが十分小さくなったら目標に合わせて止め、commit する。
- `cover="off"` のときは目標 = その時点の位置（ばねはなく、慣性が止まったら commit）。

#### 割り込み

- momentum / settle 中に pointerdown が来たら、アニメーションを止め、**見えている位置から**ジェスチャを始める。
  そのとき `M_raw` は、今の越え量に `rubberInverse` をかけて「この見え方になる生の位置」に置き直す。こうすると掴んだ瞬間に画像が跳ねない。
- `slot` や `svg` プロップが変わったら、アニメーションを目標位置まで飛ばして commit してから切り替える。

#### その他の入力

- ホイール: 1 回ごとに `M_raw` を動かし、ゴムの抵抗も効く。最後のホイールから 150ms 何もなければ settle に入る。
- キー・ボタン（縮小・拡大・回転・枠に合わせる）: 結果に `coverCorrection` を全部かけ、200ms で寄せてから commit する（ゴムは使わない）。
- `prefers-reduced-motion: reduce` のときは慣性を付けず、ばねを使わずに目標へ即座に合わせる。
- `inertia={false}` で慣性だけを切れる（ゴムとばねは残る）。

### 6.6 履歴

- commit（画像が止まったとき、ボタン、キー操作の区切り）ごとに `{ before, after }` を、**スロットごとの履歴**に積む。上限はスロットごとに 100。
- 元に戻す・やり直すは、作業中のスロットの履歴にだけ効く。コンポーネントが自分でスロットを切り替えることはない（切り替えは外が持つ。§8）。
- 連続するキー操作は 500ms 以内なら 1 つにまとめる。
- `svg` プロップが変わったら全スロットの履歴を捨てて読み直す。`slot` プロップが変わっても履歴は捨てない。

## 7. 画面のイメージ

agent-app の画面ではない独立したコンポーネントなので、agent-app の部品（`.execution-card` など）は使わない。
エディタは「描画面 1 枚 + その下のボタン 1 行」だけで、見出しや説明文は埋め込む側が持つ。
agent-app に載せる場合は白いカードの中に置き（`.log` と同じ扱い）、ボタンは `button` / `.small`、
主ボタン（保存）は埋め込む側の 1 つだけにする。

```
┌──────────────────────────────────────────────────────────┐
│ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░           │ ← 枠外: 画像全体が半透過で見える
│ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░           │
│ ░░░░░░░░╭┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄╮░░░░░░           │
│ ░░░░░░░░┆████████████████████████████┆░░░░░░           │
│ ░░░░░░░░┆██████ 枠内: 不透明 ████████┆░░░░░░  ← 市松   │ ← 画像のない所は背景
│ ░░░░░░░░┆████████████████████████████┆░░░░░░           │
│ ░░░░░░░░╰┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄╯░░░░░░           │
│ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░           │
│ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░           │
└──────────────────────────────────────────────────────────┘
 [ − ] [ + ]  [ ⟲ 90° ] [ ⟳ 90° ]  [ 枠に合わせる ]      [ ↶ ] [ ↷ ] [ リセット ]

 描画面の大きさ = 枠の外接矩形 + 各辺 25%（固定。操作では動かない）
 枠の点線は、覆えていない間だけ警告色になる
 ドラッグ: 移動 / 2 本指: 拡縮・回転・移動 / ホイール: 拡縮（Shift で回転）
 ※ 下 3 行は図の注記で、画面には出さない
```

複数画像の切り替えの入口はコンポーネントの外に置く（§8）ので、このコンポーネントの画面には現れない。

## 8. 複数画像への拡張（見越した設計）

切り替えの入口（スロットの一覧・タブ・サムネイル・文書全体の図の上でのクリックなど）は**コンポーネントの外**が持つ。
コンポーネントは「`slot` プロップで指定されたスロットを編集する」ことだけを受け持ち、切り替えの UI を描かない。
外は `onLoad` で受け取るスロットの一覧を使って入口を作り、選ばれた名前を `slot` に渡す。

```
┌ 外（アプリ）────────────────────────┐
│ スロットの一覧などの入口              │── slot="hero" ──▶ <SvgClipImageEditor>
│ 保存ボタン                            │◀── onLoad({ slots }) ──┤
│                                       │◀── onCommit(svg, { slot, M }) ──┤
└───────────────────────────────────────┘
```

初版でも次の形を採り、後から切り替えを使い始めても作り直しにならないようにする。

| 項目 | 形 | 初版での使われ方 |
|---|---|---|
| 対象の指定 | スロット名（`data-clip-slot`）。id ではなく名前を使うのは、テンプレートを差し替えても同じ名前なら同じ枠を指せるため | `slot` プロップ。省略時は文書順で最初のスロット |
| スロットの一覧 | `onLoad({ slots: { name, bboxR }[] })`。`bboxR` は枠のルート座標での外接矩形で、外が図の上に入口を描くのに使える | 使わなくてよい |
| モデル | `ClipDocModel.slots[]`（全スロットを解析） | 指定の 1 つを描く |
| 状態 | `{ transforms: Record<string, Matrix>; history: Record<string, { past; future }> }` | 1 キーだけ使う |
| 履歴 | スロットごと（§6.6）。外が切り替えを持つので、コンポーネントが元に戻す操作で勝手にスロットを変えないようにする | ふつうの履歴 |
| 出力 | `serialize(model, transforms)` が全スロットの `transform` を反映する | 編集しないスロットは元のまま |
| コールバック | `onChange(M, slot)`・`onCommit(svg, { slot, M })` のようにスロット名を最初から渡す | 常に同じ名前 |
| 制御モード | `transforms?: Record<string, Matrix>`（スロット名 → M） | 1 キーだけ使う |
| 文脈表示 | 他のスロットは文脈の層（§5.3）に現在の `transforms` で描かれる | 文脈を出したとき、他のスロットは元の位置で見える |

`slot` プロップが変わったとき:

1. 動いている慣性・ばねを目標まで飛ばして、前のスロットを commit する（§6.5 割り込み）。指が触れている途中なら、その時点の位置で終える。
2. 新しいスロットの注目領域（§4.2）を求め、`viewBox` を 200ms で寄せる（`prefers-reduced-motion` なら即座）。
   作業中の操作で `viewBox` を変えない原則は保つ。変わるのは外から明示に切り替えられたときだけ。
3. 新しいスロットの `M` は `transforms` の値（なければ `M0`）、履歴はそのスロットのもの。
4. 文脈表示を出していれば、上下の層を作り直す（前のスロットの編集結果が文脈に入る）。

## 9. 安全性とエラー

### 9.1 元の SVG を実行しない

- 元の文字列は `DOMParser` で `image/svg+xml` として解析するだけで、文書に差し込まない。
- 描画に使う要素は許可リストで組み直す:
  - `<image>`: `href`（§2.2 S5 の URL のみ。`javascript:` などは拒否）、`x` `y` `width` `height` `preserveAspectRatio`
  - クリップ形状: `rect` `circle` `ellipse` `polygon` `polyline` `path` の幾何属性（`x` `y` `width` `height` `rx` `ry` `cx` `cy` `r` `points` `d` `transform` `clip-rule`）
- 文脈表示（§5.3）は画像として読み込むので、中のスクリプトは動かない。
- 出力は元の `Document` を複製して `transform` 属性を書き換えるだけなので、元にあったものは良くも悪くもそのまま残る（サニタイズはこのコンポーネントの責務ではない）。

### 9.2 エラー

`parseClipDocument` は例外を投げず `{ ok: false, code, message, issues? }` を返す。コンポーネントは描画面の代わりに 1 行のメッセージを出し、`onError` を呼ぶ。

| code | 条件 |
|---|---|
| `parse_error` | XML として読めない、ルートが `<svg>` でない |
| `contract_violation` | §2.6 の lint でエラーがある（`issues` に一覧） |
| `no_slot` | スロットが 1 つもない、または `slot` で指定した名前がない |
| `singular_transform` | `K` / `M` / `Q` が逆行列を持たない（幅 0 の scale など） |
| `empty_clip` | クリップ形状の面積が 0（凸包が 3 点未満） |

## 10. コンポーネントの API

### 10.1 プロップ

```ts
interface SvgClipImageEditorProps {
  svg: string;                          // 元の SVG 文字列（§2 の契約を満たす）
  slot?: string;                        // 編集するスロット名。省略時は文書順で最初。変えると切り替わる（§8）

  focusPadding?: number;                // 既定 0.25
  outsideOpacity?: number;              // 既定 0.35
  context?: 'none' | 'layers';          // 既定 'none'（§5.3）
  contextAboveOpacity?: number;         // 既定 0.5
  toolbar?: boolean;                    // 既定 true

  cover?: 'elastic' | 'off';            // 既定 'elastic'（§6.4・§6.5）
  inertia?: boolean;                    // 既定 true（§6.5）
  minScale?: number;                    // 既定 0.1（読み込み時比）
  maxScale?: number;                    // 既定 20
  rotationSnap?: number;                // 度。既定 0（無効）

  transforms?: Record<string, Matrix>;  // 制御モード: 親がスロットごとの M を持つ
  defaultTransforms?: Record<string, Matrix>; // 非制御モードの初期値（既定は入力の M0）
  precision?: number;                   // 出力の e/f の小数桁。既定 3（a〜d は常に 6 桁）
  outputTransform?: 'matrix' | 'decomposed'; // 既定 'matrix'

  onLoad?: (info: { slots: { name: string; bboxR: Rect }[]; warnings: LintIssue[] }) => void;
  onChange?: (M: Matrix, slot: string) => void;                 // 操作中、毎フレーム
  onCommit?: (svg: string, edit: { slot: string; M: Matrix }) => void; // 操作の区切りごと
  onError?: (err: { code: string; message: string; issues?: LintIssue[] }) => void;

  className?: string;
  style?: React.CSSProperties;          // --clip-editor-frame / --clip-editor-warn で枠の色を変えられる
}
```

### 10.2 ref で呼べる操作

```ts
interface SvgClipImageEditorHandle {
  getSvg(): string;                           // 全スロットの現在の M を反映した出力
  getTransforms(): Record<string, Matrix>;
  reset(): void;                              // 作業中のスロットを M0 に
  fit(): void;                                // 枠に合わせる
  zoom(factor: number): void;                 // 枠の中心で
  rotate(deg: number): void;                  // 枠の中心で
  undo(): void;                               // 作業中のスロットの履歴で
  redo(): void;
}
```

## 11. 出力

`serialize(model, transforms, { precision, outputTransform })`:

1. `model.doc` を `cloneNode(true)` する。
2. 各スロットの `path` で `<image>` を辿り、`transforms[slot.name]` で `transform` を書き換える（値がないスロットは触らない）。
   - `matrix`（既定）: `matrix(a b c d e f)`。`a`〜`d` は小数 6 桁、`e` `f` は `precision` 桁で丸め、末尾の 0 を落とす。
   - `decomposed`: スキューがなければ `translate(e f) rotate(θ) scale(s)`（非一様なら `scale(sx sy)`）。スキューが残る場合は `matrix` にする。
   - 結果が恒等なら属性を消す。
3. `XMLSerializer` で文字列にする。XML 宣言・DOCTYPE・ルート直前のコメントは元にあれば残す。

変えないもの: ルートの `viewBox` / `width` / `height`、祖先・スロットの `<g>` の `transform`、`<clipPath>`、
`<image>` の `x` / `y` / `width` / `height` / `href` / `preserveAspectRatio`、飾りを含む他のすべての要素。
したがって出力は元と同じ座標系で、変わるのは「画像のどこが枠に入るか」だけになる。
出力も §2 の契約を満たしたままなので、もう一度エディタに読み込める。

### 11.1 例

入力:

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" width="800" height="600">
  <defs>
    <clipPath id="hero-clip" clipPathUnits="userSpaceOnUse">
      <path d="M200 150 H600 V450 H420 Q400 380 380 450 H200 Z"/>  <!-- 下辺に切り欠きのある枠 -->
    </clipPath>
  </defs>
  <rect width="800" height="600" fill="#f4f1ea"/>
  <g data-clip-slot="hero" clip-path="url(#hero-clip)">
    <image href="photo.jpg" x="0" y="0" width="1200" height="800"
           preserveAspectRatio="xMidYMid slice"
           transform="matrix(0.5 0 0 0.5 100 100)"/>
  </g>
  <text x="400" y="540" text-anchor="middle">見出し</text>   <!-- 飾り: 契約の外なので自由 -->
</svg>
```

画像を 1.1 倍にして 15° 回し、少し寄せた後の出力（`<image>` の `transform` だけが変わる）:

```xml
    <image href="photo.jpg" x="0" y="0" width="1200" height="800"
           preserveAspectRatio="xMidYMid slice"
           transform="matrix(0.531259 0.14235 -0.14235 0.531259 168.4 6.912)"/>
```

この枠の凸包は外側の矩形の 4 隅になる（切り欠きは凹みなので凸包に影響しない）。

## 12. テスト

| 種類 | 内容 |
|---|---|
| 単体（Node + jsdom） | `parseTransform` の各関数と合成順、`invert`、`decompose` の往復 |
| 単体 | `lintClipDocument`: §2 の規則ごとに、守る例と破る例の対を 1 つずつ。飾りを大量に足した文書（テキスト・CSS アニメーション・印のないクリップ付き画像・別のクリップ）でエラーが出ないこと |
| 単体 | `adoptClipDocument`: 各書き換えの前後で、画像の 4 隅と枠の凸包の R 座標が一致すること |
| 単体 | `parsePathData`: 全コマンドの相対・絶対、円弧の退化（半径 0・端点一致）、暗黙の繰り返し |
| 単体 | `clipHull`: 矩形・円・角丸・凹多角形・穴のある `evenodd`・3 次 / 2 次ベジェ・円弧で、(a) 真の図形の標本点がすべて凸包に入る（覆い不足を起こさない）、(b) 凸包の各頂点が真の図形から ε 以内 |
| 単体 | `pinchDelta`: a₀→a₁、b₀→b₁ を正確に写すこと。指の入れ替え（本数変化）で M が連続であること |
| 単体 | `coverCorrection`: 補正をかけた後に凸包の全頂点が D に入ること。もともと覆っているときは M を変えないこと。補正の拡大率が、拡大率を二分探索で求めた最小値と一致すること |
| 単体 | `elastic`: `rubberInverse(rubber(d)) = d`。越え量 0 で `M_view = M_raw`。越え量を増やすと見せる越え量が単調に増えて L を超えないこと。ばねが有限時間で目標に収まり、収まった M が覆い制約を満たすこと。割り込みで `M_view` が連続であること（フレームを偽の時計で進める） |
| 単体 | `splitContext`: 下の層に前の要素だけ、上の層に後ろの要素だけが残り、資源の要素は両方に残ること |
| 単体 | `serialize`: 変更なしなら出力が入力と文字列として同一。`transform` 以外の属性・要素が変わらないこと。出力が再び lint を通ること |
| 部品（Testing Library） | ポインタイベントを合成してドラッグ・2 本指・ホイールで `onChange` / `onCommit` が期待の M を返すこと。`viewBox` が操作で変わらないこと |
| 部品 | `slot` を変えると前のスロットが commit され、`viewBox` が新しいスロットの注目領域になり、前のスロットの M と履歴が保たれること |
| 実機（Playwright） | 例の SVG を読み込み、操作前後のスクリーンショットを撮る。出力 SVG を別ページで描き、エディタの枠内の画素と一致することを確かめる |

## 13. 未決事項

- ゴム・慣性・ばねの定数（0.55、0.325s、0.4s、L の取り方）は実機で触って詰める。
- `cover="elastic"` で回転したときに要る拡大を、ゴムで見せるか（今の設計）、回転中は即座に拡大して覆い続けるか。
