# HTML レポート形式

アーキテクチャレビューは、OS の一時ディレクトリに置く単一の自己完結型 HTML ファイルとしてレンダリングする。Tailwind と Mermaid はどちらも CDN から。Mermaid はグラフ的な図を確実に扱い、手作りの div とインライン SVG はより editorial な視覚（mass diagram, 断面図）を扱う。両者を混ぜる — すべてを Mermaid に頼らない。汎用的に見え始める。

## スキャフォールド

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Architecture review — {{repo name}}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script type="module">
      import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
      mermaid.initialize({ startOnLoad: true, theme: "neutral", securityLevel: "loose" });
    </script>
    <style>
      /* small custom layer for things Tailwind doesn't cover cleanly:
         dashed seam lines, hand-drawn-feeling arrow heads, etc. */
      .seam { stroke-dasharray: 4 4; }
      .leak { stroke: #dc2626; }
      .deep { background: linear-gradient(135deg, #0f172a, #1e293b); }
    </style>
  </head>
  <body class="bg-stone-50 text-slate-900 font-sans">
    <main class="max-w-5xl mx-auto px-6 py-12 space-y-12">
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

図が重みを担う。散文はまばらに、平易に、装飾なしでグロッサリ用語（[language.md](language.md)）を使う。

各候補は1つの `<article>`:

- **Title** — 短く、深化に名前を付ける（例:「Collapse the Order intake pipeline」）。
- **Badge row** — recommendation strength（`Strong` = emerald, `Worth exploring` = amber, `Speculative` = slate）と、依存カテゴリのタグ（`in-process`, `local-substitutable`, `ports & adapters`, `mock`）。
- **Files** — 等幅リスト、`font-mono text-sm`。
- **Before / After 図** — 中心。2カラム並置。下記パターン参照。
- **Problem** — 1文。何が痛むか。
- **Solution** — 1文。何が変わるか。
- **Wins** — 箇条書き、各 ≤6 語。例:「Tests hit one interface」「Pricing logic stops leaking」「Delete 4 shallow wrappers」。
- **ADR コールアウト**（該当時）— amber 背景ボックスに1行。

説明の段落は不要。図を理解するのに段落が要るなら、図を描き直す。

## 図パターン

候補に合うパターンを選ぶ。混ぜる。すべての図を同じに見せない — 多様性が要点の一部。

### Mermaid graph（依存/呼び出しフローの主力）

「X が Y を呼び Z を呼ぶ、この混乱を見よ」が要点なら Mermaid `flowchart` / `graph` を使う。Tailwind スタイルのカードに包んで唐突に見せない。classDef で leakage エッジを赤、deep モジュールを濃色に。「before: 6 往復、after: 1」にはシーケンス図が効く。

```html
<div class="rounded-lg border border-slate-200 bg-white p-4">
  <pre class="mermaid">
    flowchart LR
      A[OrderHandler] --> B[OrderValidator]
      B --> C[OrderRepo]
      C -.leak.-> D[PricingClient]
      classDef leak stroke:#dc2626,stroke-width:2px;
      class C,D leak
  </pre>
</div>
```

### 手作り boxes-and-arrows（Mermaid のレイアウトと戦うとき）

モジュールを border とラベル付きの `<div>` に。矢印は relative コンテナ上に絶対配置したインライン SVG `<line>` / `<path>`。「after」図を、内部がグレーアウトした1つの太枠 deep モジュールに見せたいときに使う — Mermaid は適切な重みで描けない。

### 断面図（層状の shallowness に良い）

水平バンド（`h-12 border-l-4`）を積んで、呼び出しが通る層を示す。before: 何もしない薄い6層。after: 統合された責任をラベルした厚い1バンド。

### Mass diagram（「インターフェースが実装と同じ幅」に良い）

モジュールごとに2つの矩形 — interface 表面積用と implementation 用。before: interface 矩形が implementation 矩形とほぼ同じ高さ（shallow）。after: interface 矩形は低く、implementation 矩形は高い（deep）。

### Call-graph collapse

before: ネストしたボックスで描く関数呼び出しの木。after: 同じ木が1つのボックスに畳まれ、内部化された呼び出しが中で薄く表示される。

## スタイル指針

- editorial 寄り、コーポレートダッシュボードではない。たっぷりの余白。見出しに serif は任意（`font-serif` は stone/slate と合う）。
- 色は控えめに: アクセント1色（emerald か indigo）＋ leakage 用の赤・警告用の amber。
- 図は ~320px の高さに保ち、before/after がスクロールなしで横並びに収まるように。
- 図内のモジュールラベルは `text-xs uppercase tracking-wider` — UI ではなく schematic に読めるように。
- スクリプトは Tailwind CDN と Mermaid ESM import のみ。レポートはそれ以外は静的 — アプリコードなし、Mermaid 自身のレンダリング以上の interactivity なし。

## Top recommendation セクション

少し大きいカード1つ。候補名・なぜか1文・そのカードへのアンカーリンク。それだけ。

## トーン

平易・簡潔 — だがアーキテクチャの名詞・動詞は [language.md](language.md) から直接来る。簡潔さは drift の言い訳にならない。

**厳密に使う:** module, interface, implementation, depth, deep, shallow, seam, adapter, leverage, locality。

**決して代替しない:** component, service, unit（→ module）・API, signature（→ interface）・boundary（→ seam）・layer, wrapper（module の意味のとき）。

**スタイルに合う言い回し:**

- 「Order intake module is shallow — interface nearly matches the implementation.」
- 「Pricing leaks across the seam.」
- 「Deepen: one interface, one place to test.」
- 「Two adapters justify the seam: HTTP in prod, in-memory in tests.」

**Wins の箇条書き**は利得をグロッサリ用語で名指す:*「locality: bugs concentrate in one module」*、*「leverage: one interface, N call sites」*、*「interface shrinks; implementation absorbs the wrappers」*。*「easier to maintain」*や*「cleaner code」*とは書かない — それらはグロッサリにない。

ヘッジなし、咳払いなし、「it's worth noting that…」なし。文が箇条書きにできるなら箇条書きにする。箇条書きが削れるなら削る。用語が [language.md](language.md) にないなら、新語を発明する前にある用語に手を伸ばす。
