# HTML レポート形式

アーキテクチャレビューは単一の自己完結型 HTML ファイルとしてレンダリングする。インライン CSS とインライン SVG / HTML だけを使い、ネットワーク、JavaScript、外部フォント、外部画像へ依存させない。`file://` で開いても図とレイアウトが崩れないことを優先する。

## 目次

- [スキャフォールド](#スキャフォールド)
- [候補カード](#候補カード)
- [図パターン](#図パターン)
- [スタイル指針](#スタイル指針)
- [Top recommendation](#top-recommendation-セクション)
- [トーン](#トーン)

## スキャフォールド

```html
<!doctype html>
<html lang="ja">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>Architecture review — {{repo name}}</title>
    <style>
      :root { color-scheme: light; --ink:#172033; --muted:#64748b; --line:#cbd5e1;
        --paper:#fafaf9; --card:#fff; --deep:#172033; --accent:#047857;
        --warn:#b45309; --leak:#b91c1c; }
      * { box-sizing:border-box; }
      body { margin:0; background:var(--paper); color:var(--ink);
        font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; }
      main { width:min(1080px,calc(100% - 32px)); margin:auto; padding:48px 0; }
      article,.recommendation { background:var(--card); border:1px solid var(--line);
        border-radius:14px; padding:24px; margin:28px 0; }
      .diagrams { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
      .diagram { min-height:280px; padding:16px; border:1px solid var(--line);
        border-radius:10px; background:#f8fafc; overflow:hidden; }
      .diagram svg { display:block; width:100%; height:auto; max-height:320px; }
      .badge { display:inline-block; margin-right:6px; padding:3px 9px;
        border-radius:999px; background:#ecfdf5; color:#065f46; font-weight:700; }
      code,.files { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; }
      .seam { stroke-dasharray:4 4; } .leak { stroke:var(--leak); }
      .deep { fill:var(--deep); color:#fff; }
      @media (max-width:760px) { .diagrams { grid-template-columns:1fr; } }
    </style>
  </head>
  <body>
    <main>
      <header>...</header>
      <section id="candidates" class="space-y-10">...</section>
      <section id="top-recommendation">...</section>
    </main>
  </body>
</html>
```

## ヘッダ

リポジトリ名・日付・コンパクトな凡例: 実線ボックス = module, 破線 = seam, 赤い矢印 = leakage, 太い濃色ボックス = deep module。導入段落なし — 直接 candidates へ。

## 候補カード

図が重みを担う。散文はまばらに、平易に、装飾なしで先に読み込んだアーキテクチャ語彙を使う。

各候補は1つの `<article>`:

- **Title** — 短く、深化に名前を付ける（例:「Collapse the Order intake pipeline」）。
- **Badge row** — recommendation strength（`Strong` = emerald, `Worth exploring` = amber, `Speculative` = slate）と、依存カテゴリのタグ（`in-process`, `local-substitutable`, `ports & adapters`, `mock`）。
- **Files** — `.files` を使った等幅リスト。
- **Before / After 図** — 中心。2カラム並置。下記パターン参照。
- **Problem** — 1文。何が痛むか。
- **Solution** — 1文。何が変わるか。
- **Wins** — 箇条書き、各 ≤6 語。例:「Tests hit one interface」「Pricing logic stops leaking」「Delete 4 shallow wrappers」。
- **ADR コールアウト**（該当時）— amber 背景ボックスに1行。

説明の段落は不要。図を理解するのに段落が要るなら、図を描き直す。

## 図パターン

候補に合うパターンを選ぶ。混ぜる。すべての図を同じに見せない — 多様性が要点の一部。

### インライン SVG（依存/呼び出しフローの主力）

「X が Y を呼び Z を呼ぶ、この混乱を見よ」が要点なら、`viewBox` 付きのインライン SVG を使う。矢印・ボックス・ラベルを直接描き、leakage は赤、deep module は濃色＋白文字にする。`role="img"` と短い `aria-label` を付ける。

```html
<div class="diagram">
  <svg viewBox="0 0 600 280" role="img" aria-label="Order intake modules before deepening">
    <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#64748b"/></marker></defs>
    <rect x="30" y="90" width="140" height="64" rx="8" fill="#fff" stroke="#64748b"/>
    <text x="100" y="126" text-anchor="middle">Order intake</text>
    <line x1="170" y1="122" x2="270" y2="122" stroke="#64748b" marker-end="url(#arrow)"/>
    <rect x="280" y="90" width="140" height="64" rx="8" fill="#fff" stroke="#64748b"/>
    <text x="350" y="126" text-anchor="middle">Validation</text>
  </svg>
</div>
```

### HTML boxes-and-arrows（単純な関係に使う）

モジュールを border とラベル付きの `<div>` にする。矢印だけインライン SVG `<line>` / `<path>` を使う。「after」図を、内部がグレーアウトした1つの太枠 deep module に見せたいときに向く。

### 断面図（層状の shallowness に良い）

水平バンド（`h-12 border-l-4`）を積んで、呼び出しが通る層を示す。before: 何もしない薄い6層。after: 統合された責任をラベルした厚い1バンド。

### Mass diagram（「インターフェースが実装と同じ幅」に良い）

モジュールごとに2つの矩形 — interface 表面積用と implementation 用。before: interface 矩形が implementation 矩形とほぼ同じ高さ（shallow）。after: interface 矩形は低く、implementation 矩形は高い（deep）。

### Call-graph collapse

before: ネストしたボックスで描く関数呼び出しの木。after: 同じ木が1つのボックスに畳まれ、内部化された呼び出しが中で薄く表示される。

## スタイル指針

- editorial 寄り、コーポレートダッシュボードではない。たっぷりの余白。見出しに serif は任意（`font-serif` は stone/slate と合う）。
- 色は控えめに: アクセント1色（emerald か indigo）＋ leakage 用の赤・警告用の amber。
- 図コンテナは `min-height: 280px`、SVG は `viewBox` と `max-height: 320px` を使う。固定ピクセル高へ無理に押し込めない。
- 図内のモジュールラベルは小さく字間を広げ、UI ではなく schematic に読めるようにする。
- スクリプトは置かない。HTML単体を別端末へコピーしても同じ表示になる状態を保つ。

## Top recommendation セクション

少し大きいカード1つ。候補名・なぜか1文・そのカードへのアンカーリンク。それだけ。

## トーン

平易・簡潔 — だがアーキテクチャの名詞・動詞は先に読み込んだ語彙定義から直接来る。簡潔さは drift の言い訳にならない。

**厳密に使う:** module, interface, implementation, depth, deep, shallow, seam, adapter, leverage, locality。

**決して代替しない:** component, service, unit（→ module）・API, signature（→ interface）・boundary（→ seam）・layer, wrapper（module の意味のとき）。

**スタイルに合う言い回し:**

- 「Order intake module is shallow — interface nearly matches the implementation.」
- 「Pricing leaks across the seam.」
- 「Deepen: one interface, one place to test.」
- 「Two adapters justify the seam: HTTP in prod, in-memory in tests.」

**Wins の箇条書き**は利得をグロッサリ用語で名指す:*「locality: bugs concentrate in one module」*、*「leverage: one interface, N call sites」*、*「interface shrinks; implementation absorbs the wrappers」*。*「easier to maintain」*や*「cleaner code」*とは書かない — それらはグロッサリにない。

ヘッジなし、咳払いなし、「it's worth noting that…」なし。文が箇条書きにできるなら箇条書きにする。箇条書きが削れるなら削る。定義済みの語彙にないなら、新語を発明する前にある用語に手を伸ばす。
