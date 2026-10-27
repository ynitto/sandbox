# SVG クリップ画像エディタ 設計

SVG に含まれる「クリップされた画像」の見え方（どの部分がクリップ枠に収まるか）を、
マウスまたはタッチで画像を動かして合わせ込む React コンポーネントの設計。
出力は、元の SVG と同じ座標系のまま画像の座標だけを書き換えた SVG。

> 状態: 設計（未実装）。画面のイメージ（§7）の承認を経てから実装に入る。
> 実装の置き場所は承認時に決める（案: `tools/svg-clip-image-editor/`）。

## 1. 要件と方針

| 要件 | 決めたこと |
|---|---|
| クリップされた画像の、枠に入る領域を更新する | 変えるのは対象 `<image>` の `transform` 属性**だけ**。`x` / `y` / `width` / `height` / `href` / クリップ形状 / 他の要素には触れない |
| クリップ枠は固定、画像を拡大縮小・回転・平行移動する | 編集は「クリップ空間（§3）で画像に左から掛ける相似変換 Δ」に一本化する。スキューは作らない（元の `transform` にあるスキューは保つ） |
| 編集 UI はクリップ枠の近辺に注目して表示する | エディタの `viewBox` を「クリップ枠のルート座標での外接矩形＋余白」に固定する |
| 注目領域は拡大縮小・回転・平行移動できない | `viewBox` は読み込み時に 1 回だけ決め、操作で変えない。ブラウザ自体のピンチズーム・スクロールも止める（§5.5） |
| マウス・タッチで画像を合わせ込む | Pointer Events に統一。1 本指/左ドラッグ = 移動、2 本指 = 拡縮＋回転＋移動、ホイール = 拡縮（Shift で回転） |
| 枠内は透明（画像が不透明に見える）、枠外は半透過で画像全体が透ける | 画像を 2 枚重ねる。下は全体を `opacity` で半透過、上はクリップ枠で切り抜いた不透明の画像（§5.2） |
| 出力は元と同じ座標系 | ルートの `viewBox` / `width` / `height` と、対象画像より上の要素の `transform` は変えない。画像の `transform` を `matrix(...)` で書き直すだけ |

採らなかったもの:

- **クリップ形状を逆向きに動かす実装**（画像を固定して枠を動かす）: 出力でクリップを書き換えることになり、
  共有された `<clipPath>` を壊す。
- **`<canvas>` で描いて合成する実装**: 外部 URL の画像で CORS の制約を受け、SVG のベクタ性も失う。
  SVG のまま描けば同じ描画エンジンで出力と寸分違わず見える。
- **元の SVG 文字列を DOM にそのまま差し込む実装**: スクリプトやイベント属性を実行しうる。
  エディタは必要な要素だけを組み直して描く（§8）。

## 2. 入力 SVG の構造定義

### 2.1 正規形

エディタが直接扱う形（正規形）は次のとおり。

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="vx vy vw vh" width="…" height="…">
  <defs>
    <clipPath id="CLIP" clipPathUnits="userSpaceOnUse" [transform="Q"]>
      <!-- rect / circle / ellipse / polygon / polyline / path の 1 つ以上（和集合） -->
    </clipPath>
  </defs>

  <g [transform="…"]>                          <!-- 祖先: 任意段数。transform は K に積まれる -->
    <g clip-path="url(#CLIP)" [transform="…"]>  <!-- ラッパー: クリップを持つ要素。clip 空間を定める -->
      <g [transform="J"]>                       <!-- 中間: 任意段数（0 段でよい） -->
        <image id="photo" data-clip-target=""
               href="…" x="…" y="…" width="…" height="…"
               preserveAspectRatio="…"
               transform="M"/>                  <!-- 編集対象。書き換えるのは transform だけ -->
      </g>
    </g>
  </g>
</svg>
```

要点は **クリップを持つ要素（ラッパー）と、動かす要素（`<image>`）が別であること**。
`clipPathUnits="userSpaceOnUse"` の座標は「クリップを参照する要素のユーザー空間」で解釈され、
その要素自身の `transform` も含む。`<image clip-path="…" transform="…">` のように同じ要素に両方があると、
画像の `transform` を変えた瞬間に枠も一緒に動いてしまう。

### 2.2 対象画像の選び方

1. `target` プロップ（`<image>` の id）が渡されていればそれ。
2. なければ `data-clip-target` 属性を持つ `<image>`。
3. なければ、自身か祖先に `clip-path` を持つ最初の `<image>`。
4. どれもなければエラー（§8.2）。

`clip-path` は属性と `style="clip-path:url(#…)"` の両方を読む。CSS のクラス経由のクリップは読まない（エラー）。

### 2.3 読み込み時の正規化

正規形でない入力は、**描画結果を変えない**書き換えで正規形にしてから編集する。
書き換えは出力にも残るので、`onLoad` で何を書き換えたかを返す。

| 入力 | 正規化 |
|---|---|
| `<image>` 自身が `clip-path` を持つ | `<g clip-path="…" transform="元の transform">` で包み、`<image>` から `clip-path` を外して `transform` を恒等にする。見た目は同一 |
| `clipPathUnits="objectBoundingBox"` | 参照要素の外接矩形が画像の移動で変わり、枠が動いてしまう。読み込み時点の外接矩形 `(bx, by, bw, bh)` で `userSpaceOnUse` に焼き直した**複製**の `<clipPath>`（新しい id、`transform="matrix(bw 0 0 bh bx by)"` を Q の前に積む）を作り、ラッパーの参照先を差し替える。元の `<clipPath>` は他の参照があり得るので残す |
| ルートに `viewBox` がない | エディタ内部でだけ `0 0 width height` とみなす。出力には足さない |
| `xlink:href` だけがある | そのまま使い、出力でも `xlink:href` のまま残す |

### 2.4 扱わない入力（エラーにする）

- 対象画像の祖先に入れ子の `<svg>` や `<symbol>`、`<use>` 経由の参照がある（ビューポート変換が入る）
- 祖先や対象に CSS の `transform` / `transform-origin`（`style` 属性）がある
- 対象画像とラッパーの両方が別々のクリップを持つ（どちらの枠に合わせるか決まらない）
- `<clipPath>` の中に `<use>` / `<text>` がある（外接矩形を決定的に求められない）

## 3. 座標系

行列は SVG の `matrix(a b c d e f)` の 6 要素で表し、点 `p` への適用を `X·p` と書く。

| 記号 | 空間 | 定義 |
|---|---|---|
| R | ルート | ルート `<svg>` の `viewBox` 座標。出力の座標系そのもの |
| C | クリップ空間 | ラッパーのユーザー空間。クリップ形状はここで定義される |
| I | 画像ローカル | `<image>` の `x` / `y` / `width` / `height` が置かれる空間 |
| P | クリップ形状 | `<clipPath>` の子の座標 |
| S | スクリーン | ブラウザの CSS ピクセル（ポインタの `clientX` / `clientY`） |

```
p_R = K · p_C          K = ルート直下からラッパー自身までの transform の積
p_C = J · M · p_I      J = ラッパーの子から <image> の親までの transform の積、M = <image> の transform
p_C = Q · p_P          Q = <clipPath> の transform（なければ恒等）
p_S = V · p_R          V = エディタの <svg> の getScreenCTM()
```

`K` / `J` / `Q` / `M` は `transform` 属性の文字列を自前で解析して求める
（`matrix` / `translate` / `scale` / `rotate(θ [cx cy])` / `skewX` / `skewY`）。
`getCTM()` に頼らないのは、元の SVG を DOM に載せずに Node だけで単体テストできるようにするため。

### 3.1 編集の表し方

編集は C 空間で左から掛ける変換 Δ で表す（枠が C 空間で固定なので、操作の中心や移動量を枠と同じ空間で扱える）。

```
J · M_new = Δ · J · M_start
M_new     = J⁻¹ · Δ · J · M_start
```

中間の `J` がなければ（よくある形）`M_new = Δ · M_start`。
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
   │ parseClipDocument()            DOMParser で解析 → 対象特定 → 正規化 → K/J/Q/M/枠の外接矩形
   ▼
ClipDocModel ───────────────┐
   │                        │ serialize(model, M)   元の Document を複製し <image transform> だけ差し替え
   ▼                        ▼
<SvgClipImageEditor>      出力 svg 文字列
   ├ useFocusViewBox()      注目領域（固定の viewBox）
   ├ useImageGesture()      Pointer / Wheel / Key → Δ → M
   ├ applyCover()           （任意）枠を画像で覆う制約
   └ useHistory()           元に戻す / やり直す
```

| モジュール | 責務 | 主な関数 |
|---|---|---|
| `matrix.ts` | 2D アフィン行列 | `multiply` `invert` `apply` `translate` `scale` `rotate` `parseTransform` `formatMatrix` `decompose` |
| `parse.ts` | 入力の解析と正規化 | `parseClipDocument(svg, opts): Result<ClipDocModel>` |
| `geometry.ts` | クリップ形状の外接矩形、画像の描画矩形 | `clipBBox(shapes, Q)` `drawnImageRect(image, natural?)` `focusRect(model, padding)` |
| `gesture.ts` | ポインタ列 → Δ | `panDelta` `pinchDelta` `wheelDelta` |
| `constraint.ts` | 覆い制約・拡縮の上下限 | `applyCover(model, M)` `clampScale(M, M0, min, max)` |
| `serialize.ts` | 出力 | `serialize(model, M, precision): string` |
| `SvgClipImageEditor.tsx` | 描画と操作 | — |

### 4.1 `ClipDocModel`

```ts
type Matrix = [a: number, b: number, c: number, d: number, e: number, f: number];
type Rect = { x: number; y: number; width: number; height: number };

interface ClipDocModel {
  doc: Document;                 // 正規化済みの元文書（出力の土台。描画には使わない）
  imagePath: number[];           // doc 内の対象 <image> への子インデックス列（id がなくても辿れる）
  rootViewBox: Rect;             // R
  K: Matrix;                     // C → R
  J: Matrix;                     // 画像の親 → C
  M0: Matrix;                    // 読み込み時の <image> の transform
  image: {                       // 描画用に組み直した属性（許可リストのみ）
    href: string; hrefAttr: 'href' | 'xlink:href';
    x: number; y: number; width: number; height: number;
    preserveAspectRatio: string;
  };
  clip: {
    Q: Matrix;
    shapes: ClipShape[];         // { tag: 'rect' | 'circle' | …, attrs: 幾何属性のみ }
    bboxC: Rect;                 // C 空間での外接矩形（Q を適用済み）
  };
  normalized: string[];          // §2.3 で行った書き換えの一覧（onLoad で返す）
}
```

### 4.2 注目領域（固定の viewBox）

1. 枠の外接矩形 `bboxC` の 4 隅を `K` で R に写し、その外接矩形 `F` を取る（ラッパーが回転していても枠全体が入る）。
2. `F` の各辺に `focusPadding`（既定 0.25 = 幅・高さの 25%）の余白を足す。
3. それをエディタの `viewBox` にして `preserveAspectRatio="xMidYMid meet"` で表示する。

向きはルート座標のまま（出力と同じ向き）。`viewBox` は `model` が変わったときだけ計算し、操作では変えない。
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

  {/* 任意: 文脈（対象画像を除いた元の SVG を <image> として下に敷く。§5.3） */}

  <g transform={K}>
    <g transform={J}>
      {/* ① 画像全体（半透過）: 枠の外にはみ出した部分が透けて見える */}
      <image {...image} transform={M} opacity={outsideOpacity} />
    </g>
    {/* ② 枠で切り抜いた画像（不透明） */}
    <g clipPath={`url(#${uid}-clip)`}>
      <g transform={J}><image {...image} transform={M} /></g>
    </g>
    {/* ③ 枠の輪郭（太さは画面上で一定） */}
    <g transform={Q} fill="none" pointerEvents="none">
      {clip.shapes.map(s => <Shape {...s} stroke="currentColor"
                                  strokeDasharray="6 4" vectorEffect="non-scaling-stroke" />)}
    </g>
  </g>

  {/* ④ 当たり判定: viewBox 全面。画像の外を掴んでも画像が動く */}
  <rect x={focus.x} y={focus.y} width={focus.width} height={focus.height} fill="transparent" />
</svg>
```

- ② のクリップは ① と同じ `K` の中に置くので、`userSpaceOnUse` の座標は元の SVG とまったく同じ意味になる。
- `id` は `useId()` で接頭辞を付け、同じページに複数置いても衝突しない。
- 同じ `href` の `<image>` を 2 枚描くが、ブラウザはデコード結果を共有するので負荷はほぼ 1 枚分。

### 5.2 「枠内は透明、枠外は半透過」の見え方

| 領域 | 見え方 |
|---|---|
| 枠の内側 | ② が不透明で重なり、出力と同じ見え方になる（枠に覆いがない = 透明） |
| 枠の外で画像がある所 | ① だけが `outsideOpacity`（既定 0.35）で見える。画像全体の位置関係が分かる |
| 画像がない所 | 背景（市松模様、または §5.3 の文脈） |

### 5.3 文脈表示（任意、既定は出さない）

`context="below"` のとき、対象画像を取り除いた元の SVG を `data:image/svg+xml` にして、
`<image x=vx y=vy width=vw height=vh preserveAspectRatio="none">` として最下層に敷く。
画像として読み込まれた SVG ではスクリプトが動かないので安全で、元文書の `id` とエディタの `id` も衝突しない。
ただし元文書で画像より上に描かれる要素（飾り枠など）も下に敷かれるので、見え方は出力と完全には一致しない。

### 5.4 ツールバー

エディタの下に 1 行。ボタンだけを置き、操作の説明文は常駐させない（説明は README に書く）。

| ボタン | 動き |
|---|---|
| 縮小 / 拡大 | 枠の中心を中心に ×1/1.1 / ×1.1 |
| 左に 90° / 右に 90° | 枠の中心を中心に回転 |
| 枠に合わせる | 現在の回転のまま、枠を覆う最小の拡大率にして中央に置く（§6.4） |
| 元に戻す / やり直す | 履歴（§6.5） |
| リセット | 読み込み時の `M0` に戻す |

`toolbar={false}` で隠し、ref のメソッド（§9.2）で外から同じ操作を呼べる。

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
    │       pointerup(最後の1本)   │  └──────────────────────────┘
    └──────────────────────────────┘    pointerup(2本→1本): 残った指で pan に戻る（基準を取り直す）
       → commit（履歴に積む・onCommit）
```

- 3 本目以降の指は無視する。
- 本数が変わるたびに**基準を取り直す**: `M_start ← 現在の M`、開始点 ← 各ポインタの現在位置。こうすると指を足し引きしても画像が跳ねない。
- `setPointerCapture` で、指が枠や `<svg>` の外へ出ても追従する。`pointercancel` は `pointerup` と同じに扱う。
- `pointermove` は `requestAnimationFrame` で 1 フレーム 1 回にまとめて `M` を更新する。

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

`rotationSnap`（既定 0 = 無効、例 3）を与えると、2 本指の回転で全体の回転角が 90° の倍数 ± `rotationSnap`° に入ったとき吸着させる。

### 6.3 拡大率の上下限

全体の拡大率を `σ(M) = sqrt(|det(J·M)|)` とし、`σ(M) / σ(M0)` が `[minScale, maxScale]`（既定 `[0.1, 20]`）に
収まるよう、Δ の `s`（または `k`）を詰める。

### 6.4 覆い制約（`constraint="cover"`、既定 `"none"`）

「枠の内側に画像のない所を作らない」制約。ジェスチャ中は自由に動かせるようにし、
**指を離したとき（commit の直前）**に最小限の補正をかけて 150ms で寄せる。

画像の描画矩形 `D = [x₀, x₁] × [y₀, y₁]`（I 空間）は `preserveAspectRatio` で決まる:
`none` と `slice` は `x / y / width / height` の矩形そのもの、`meet` は画像の元の大きさ（読み込んで `naturalWidth` / `naturalHeight` を取る）から求めた内側の矩形。

1. 枠の外接矩形 `bboxC` の 4 隅を I 空間に写す: `q_i = (J·M)⁻¹ · corner_i`。その I 空間での外接矩形を `[qx₀, qx₁] × [qy₀, qy₁]` とする。
   （枠の形は 4 隅の凸包に含まれ、`D` は凸なので、4 隅が `D` に入れば枠全体が覆われる。円や角丸では少し保守的になる）
2. 幅か高さが足りなければ拡大する: `k = max((qx₁−qx₀)/(x₁−x₀), (qy₁−qy₀)/(y₁−y₀))`。
   `k > 1` なら枠の中心 `c` を中心に `Δ = T(c)·S(k)·T(−c)` を掛け、1 を計算し直す。
3. 平行移動を I 空間で求める（画像を I 空間で `u` 動かすと `q` は `−u` 動く）:
   `u_x = clamp(0, qx₁ − x₁, qx₀ − x₀)`、`u_y` も同様。`M ← M · T(u)`。

「枠に合わせる」ボタンは同じ手順で、2 の `k` を 1 未満も許し（縮めて丁度覆う）、3 の `u` を中央寄せ
（`u_x = (qx₀ + qx₁)/2 − (x₀ + x₁)/2`）にしたもの。

### 6.5 履歴

- commit（ジェスチャの終わり、ボタン、キー操作の区切り）ごとに `M` を積む。上限 100。
- 連続するキー操作は 500ms 以内なら 1 つにまとめる。
- `svg` プロップが変わったら履歴を捨てて読み直す。

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
 ドラッグ: 移動 / 2 本指: 拡縮・回転・移動 / ホイール: 拡縮（Shift で回転）
 ※ 下 2 行は図の注記で、画面には出さない
```

## 8. 安全性とエラー

### 8.1 元の SVG を実行しない

- 元の文字列は `DOMParser` で `image/svg+xml` として解析するだけで、文書に差し込まない。
- 描画に使う要素は許可リストで組み直す:
  - `<image>`: `href`（`data:image/*` / `https:` / `http:` / `blob:` / 相対 URL のみ。`javascript:` などは拒否）、`x` `y` `width` `height` `preserveAspectRatio`
  - クリップ形状: `rect` `circle` `ellipse` `polygon` `polyline` `path` の幾何属性（`x` `y` `width` `height` `rx` `ry` `cx` `cy` `r` `points` `d` `transform` `clip-rule`）
- 文脈表示（§5.3）は画像として読み込むので、中のスクリプトは動かない。
- 出力は元の `Document` を複製して `transform` 属性を 1 つ書き換えるだけなので、元にあったものは良くも悪くもそのまま残る（サニタイズはこのコンポーネントの責務ではない）。

### 8.2 エラー

`parseClipDocument` は例外を投げず `{ ok: false, code, message }` を返す。コンポーネントは描画面の代わりに 1 行のメッセージを出し、`onError` を呼ぶ。

| code | 条件 |
|---|---|
| `parse_error` | XML として読めない、ルートが `<svg>` でない |
| `no_target` | 対象の `<image>` が見つからない |
| `no_clip` | 対象にクリップがかかっていない |
| `unsupported_structure` | §2.4 のいずれか |
| `singular_transform` | `K` / `J` / `M` / `Q` が逆行列を持たない（幅 0 の scale など） |

## 9. コンポーネントの API

### 9.1 プロップ

```ts
interface SvgClipImageEditorProps {
  svg: string;                          // 元の SVG 文字列
  target?: string;                      // 対象 <image> の id（§2.2）

  focusPadding?: number;                // 既定 0.25
  outsideOpacity?: number;              // 既定 0.35
  context?: 'none' | 'below';           // 既定 'none'（§5.3）
  toolbar?: boolean;                    // 既定 true

  constraint?: 'none' | 'cover';        // 既定 'none'（§6.4）
  minScale?: number;                    // 既定 0.1（読み込み時比）
  maxScale?: number;                    // 既定 20
  rotationSnap?: number;                // 度。既定 0（無効）

  transform?: Matrix;                   // 制御モード: 親が M を持つ
  defaultTransform?: Matrix;            // 非制御モードの初期値（既定は入力の M0）
  precision?: number;                   // 出力の e/f の小数桁。既定 3（a〜d は常に 6 桁）
  outputTransform?: 'matrix' | 'decomposed'; // 既定 'matrix'

  onLoad?: (info: { normalized: string[] }) => void;
  onChange?: (M: Matrix) => void;       // 操作中、毎フレーム
  onCommit?: (svg: string, M: Matrix) => void; // 操作の区切りごと
  onError?: (err: { code: string; message: string }) => void;

  className?: string;
  style?: React.CSSProperties;
}
```

### 9.2 ref で呼べる操作

```ts
interface SvgClipImageEditorHandle {
  getSvg(): string;                     // 現在の M を反映した出力
  getTransform(): Matrix;
  reset(): void;
  fit(): void;                          // 枠に合わせる
  zoom(factor: number): void;           // 枠の中心で
  rotate(deg: number): void;            // 枠の中心で
  undo(): void;
  redo(): void;
}
```

## 10. 出力

`serialize(model, M, precision)`:

1. `model.doc` を `cloneNode(true)` する（正規化済み。§2.3）。
2. `imagePath` で対象 `<image>` を辿り、`transform` を書き換える。
   - `matrix`（既定）: `matrix(a b c d e f)`。`a`〜`d` は小数 6 桁、`e` `f` は `precision` 桁で丸め、末尾の 0 を落とす。
   - `decomposed`: スキューがなければ `translate(e f) rotate(θ) scale(s)`（非一様なら `scale(sx sy)`）。スキューが残る場合は `matrix` にする。
   - 結果が恒等なら属性を消す。
3. `XMLSerializer` で文字列にする。XML 宣言・DOCTYPE・ルート直前のコメントは元にあれば残す。

変えないもの: ルートの `viewBox` / `width` / `height`、祖先・ラッパー・中間要素の `transform`、`<clipPath>`、
`<image>` の `x` / `y` / `width` / `height` / `href` / `preserveAspectRatio`、他のすべての要素。
したがって出力は元と同じ座標系で、変わるのは「画像のどこが枠に入るか」だけになる。

### 10.1 例

入力:

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" width="800" height="600">
  <defs>
    <clipPath id="photoClip" clipPathUnits="userSpaceOnUse">
      <rect x="200" y="150" width="400" height="300" rx="24"/>
    </clipPath>
  </defs>
  <rect width="800" height="600" fill="#f4f1ea"/>
  <g clip-path="url(#photoClip)">
    <image id="photo" data-clip-target="" href="photo.jpg"
           x="0" y="0" width="1200" height="800" preserveAspectRatio="xMidYMid slice"
           transform="matrix(0.5 0 0 0.5 100 100)"/>
  </g>
</svg>
```

画像を 1.1 倍にして 15° 回し、少し寄せた後の出力（`<image>` の `transform` だけが変わる）:

```xml
    <image id="photo" data-clip-target="" href="photo.jpg"
           x="0" y="0" width="1200" height="800" preserveAspectRatio="xMidYMid slice"
           transform="matrix(0.531259 0.142350 -0.142350 0.531259 168.4 6.912)"/>
```

## 11. テスト

| 種類 | 内容 |
|---|---|
| 単体（Node + jsdom） | `parseTransform` の各関数と合成順、`invert`、`decompose` の往復 |
| 単体 | `parseClipDocument`: 正規形・§2.3 の各正規化・§2.4 の各エラー。正規化の前後で、画像の 4 隅とクリップの 4 隅の R 座標が一致すること |
| 単体 | `pinchDelta`: a₀→a₁、b₀→b₁ を正確に写すこと。指の入れ替え（本数変化）で M が連続であること |
| 単体 | `applyCover`: 補正後に枠の 4 隅が D に入ること。もともと覆っているときは M を変えないこと |
| 単体 | `serialize`: Δ = 恒等なら出力が入力と意味的に同一（正規化がない入力では文字列として同一）。`transform` 以外の属性・要素が変わらないこと |
| 部品（Testing Library） | ポインタイベントを合成してドラッグ・2 本指・ホイールで `onChange` / `onCommit` が期待の M を返すこと。`viewBox` が操作で変わらないこと |
| 実機（Playwright） | 例の SVG を読み込み、操作前後のスクリーンショットを撮る。出力 SVG を別ページで描き、エディタの枠内の画素と一致することを確かめる |

## 12. 未決事項

- 複数の画像（同じ枠を共有する画像が複数ある、枠ごとに画像がある）を 1 つのエディタで切り替えるか。現状は 1 エディタ = 1 画像。
- `constraint="cover"` をジェスチャ中にも効かせる（ゴムのような抵抗）か。現状は指を離したときだけ。
- 文脈表示（§5.3）で、画像より上に描かれる要素を上に重ねて出し分けるか。
