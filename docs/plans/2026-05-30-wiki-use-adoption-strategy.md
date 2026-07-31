# wiki-use 普及戦略 — 「育たない Wiki」の原因分析と改善策

> 課題提起: 「wiki-use がまったく育たない。LLM Wiki の考え方を導入したが、Obsidian からの取り込み以外に ingest も query も使われていない。」

参考（他実装・他者の使い方の調査）:
- [Karpathy LLM Wiki の解説（MindStudio）](https://www.mindstudio.ai/blog/andrej-karpathy-llm-wiki-knowledge-base-claude-code)
- [Astro-Han/karpathy-llm-wiki（Agent Skills 互換の参照実装）](https://github.com/Astro-Han/karpathy-llm-wiki)
- [Karpathy shares 'LLM Knowledge Base' architecture（VentureBeat）](https://venturebeat.com/data/karpathy-shares-llm-knowledge-base-architecture-that-bypasses-rag-with-an)
- [I used Karpathy's LLM Wiki to build a knowledge base that maintains itself（Medium / Balu Kosuri）](https://medium.com/@k.balu124/i-used-karpathys-llm-wiki-to-build-a-knowledge-base-that-maintains-itself-with-ai-df968e4f5ea0)
- [agent-wiki — hosted markdown memory for agents](https://agent-wiki.justin-ccf.workers.dev/)
- 検索ツール調査: [tobi/qmd](https://github.com/tobi/qmd)（ローカル CLI 検索エンジン）、[Yakitrak/notesmd-cli](https://github.com/Yakitrak/notesmd-cli)（Obsidian CLI コミュニティ版）、[Obsidian 公式 CLI](https://obsidian.md/cli)

---

## 0. 結論（先に要約）

wiki-use が育たない根本原因は「プロアクティブ指示が足りないから」ではなく、**構造的な 4 点**にある:

1. **コールドスタートの死のスパイラル** — 空の Wiki → 検索が当たらない → 「使えない」と学習 → 検索しなくなる → 中身が増えない、の自己強化ループ。
2. **ltm-use との境界が未定義** — 「知識を残す」自律トリガーをすべて tier:core の `ltm-use` が握っており、エージェントの保存反射は `save_memory.py` に向かう。wiki-use は明示コマンドの余り物しか受け取れていない。
3. **取り込みが「明示コマンド」依存** — 通常作業で生まれる知識（調査結果・Web リサーチ・設計判断）が ingest されない。実際に発火する習慣は Obsidian の一括取り込みだけ。
4. **検索の貧弱さ** — 単一キーワードの小文字部分一致のみ。日本語・表記ゆれ・別名に弱く、中身があっても当たらず、コールドスタートを悪化させる。

改善の方向性は **「捕捉を限りなくゼロ摩擦の副作用にする」「検索のペイオフを上げる」「ltm-use との役割を線引きする」** の 3 本。

---

## 1. 他者の使い方の調査（What works elsewhere）

### 1.1 Karpathy 原典のパターン

- 構造は `raw/`（**不変のソース原本**）＋ `wiki/`（LLM が合成する知識ページ）＋ `index.md` ＋ `log.md`。
- 役割分担は明確: **人間はソースを選び・良い問いを立てる／LLM は Wiki を保守する**。
- ingest・query は基本的に**明示**（"Ingest this article: URL" / "What do I know about X?"）。完全自律ではない。
- RAG に対する優位は **compounding**: 合成は ingest 時に一度行い、次の query は「原本」ではなく「合成済みページ」から始まる。

### 1.2 実運用で「定着する」要因（実装者の声）

- **捕捉の習慣化と低摩擦化**: クリップ → `raw/` → 「Wiki 更新して」を日課にする。変更ファイルを検知して自動 ingest する watch スクリプトを併用する例が多い。
- **Obsidian を人間側の閲覧・編集 UI にする**: `[[wikilink]]` がそのまま機能し、グラフビューで成長を可視化できる。
- **MCP エンドポイント化（agent-wiki / Link 等）**: read/write/search をエージェントから 1 ステップで叩けるようにし、捕捉と参照の摩擦をほぼゼロにしている。「Wiki = ストレージ層」という位置づけ。

### 1.3 本リポジトリ実装との差分

| 観点 | Karpathy 原典 / 他実装 | 本リポジトリ wiki-use |
|------|------------------------|------------------------|
| ソース原本 | `raw/` に**不変保管**（再合成・監査・重複検出が可能） | wiki_root **外**で各自管理・コピーしない（取り込み済み判定が不安定） |
| 取り込み契機 | 明示が基本だが**捕捉を日課化**／watch 自動化 | 明示 or「URL+読んで」のみ。日課化されたのは Obsidian 一括取り込みだけ |
| 参照契機 | 明示 query が中心 | 「回答前に必ず検索」を標榜（後述のとおり発火しない） |
| 競合する記憶層 | （単独） | tier:core の `ltm-use` が自律捕捉トリガーを総取り |
| 検索 | 実装依存（多くは tokenized / semantic） | 単一キーワードの小文字部分一致のみ |

> **示唆**: 原典は「明示でも回る」よう**捕捉を人間の日課**にしている。本実装は捕捉を自動化しようとしたが、トリガーは ltm-use に奪われ、明示の日課も Obsidian しか定着しなかった。結果としてどちらの経路も機能していない。

---

## 2. 根本原因の分析（Why it doesn't grow）

### RC1: コールドスタートの死のスパイラル
空の Wiki では `wiki_query.py search` がほぼ必ず空振りする。エージェントは「当たらない＝使えない」と早期に学習し検索をやめる。ingest は明示依存なので Obsidian 一括以外で中身は増えない。→ 永遠に空に近いまま。**自己強化ループ**。

### RC2: ltm-use との境界が未定義（最重要）
`common.instructions.md` で `ltm-use`（tier:core）は次の自律捕捉トリガーを総取りしている:
- 原因特定が難しかったバグと解決策／設計判断とトレードオフ／ユーザーの肯定反応直後／エラー解決時／新ツール初使用／繰り返しパターン／セッション終了時のまとめ。

「残す価値がある知見」を見つけた瞬間、エージェントの反射は `save_memory.py` に向かう。wiki-use が受け持つのは「明示 ingest」と「調査時 query」だけで、その query すらセッション開始の ltm recall（手順 3）と競合する。**両者は "knowledge persistence" で大きく重複しているのに役割の線引きがない。** ltm-use が育ち wiki-use が飢える構造。

### RC3: 取り込みが明示コマンド依存で、organic capture が無い
通常作業で生成される知識（コード調査の結論、Web リサーチ結果、ドキュメント精読、横断的な合成回答）は ingest されない。発火するのは「wikiに取り込んで」「URL+読んで」と、obsidian-use 経由の一括取り込みのみ。前者は人間が能動的に言わない限り起きず、定着したのは後者だけ。

### RC4: プロアクティブ指示が「読み込まれない場所」にある
「回答前に必ず wiki を検索」「URL を受け取ったら自動 ingest」という強い規則は **SKILL.md の "プロアクティブな操作" 節**にある。しかしスキル本文は**そのスキルが description マッチで起動したときにしか読み込まれない**。「アテンションについて教えて」のような一般質問ではスキルが自動ロードされず、自分自身の「検索してから答えよ」が実行されない。常時適用される唯一の層は `common.instructions.md` だが、そこでの wiki query は (a) 条件付き（"設定されている場合"）、(b) 長文の中盤に埋没、(c) 弱い語調（"ファーストチョイス"）、(d) 先に現れる ltm recall に主導権を奪われている。→ プロアクティブ性は構造的に弱い。

### RC5: 検索が貧弱でペイオフが低い
`wiki_query.py search` は単一キーワードの `keyword in text`（小文字化部分一致）。トークン化・複数語・別名・frontmatter 重み付け無し。日本語＋単一部分一致では「アテンション機構」で検索しても「注意機構」「attention」を取りこぼす。中身があっても当たらず、RC1 を悪化させる。

### RC6: `raw/` が無く compounding ループが弱い
原典は `raw/` にソースを不変保管し、再合成・監査・重複検出を可能にする。本実装はソースを外部管理・コピーしないため、(a) 何を取り込んだかの記録が残らない、(b) 再合成できない、(c) 「取り込み済みか」の事前 search（RC5 で不安定）に依存して重複・取りこぼしが起きる。compounding（合成の積み上げ）が弱い。

### RC7: 価値が可視化されず、フィードバックループが無い
ltm-use は rate / consolidate / index を持つ。wiki-use は lint（整合性）のみで、「Wiki が役に立った」体験や成長量を surface する仕組みが無い。ユーザーが効果を実感できず、手が伸びない。

---

## 3. 改善戦略

テーマ: **捕捉をゼロ摩擦の副作用に／検索のペイオフを上げる／ltm-use と線引きする。**

### Phase 0 — ltm-use との境界を定義する（最優先・低コスト）

役割を明文化し、`common.instructions.md` と両 SKILL.md に反映する:

| | ltm-use | wiki-use |
|--|---------|----------|
| 記憶の種類 | 手続き的・エピソード的（「自分／チームの仕事の仕方」） | 意味的・参照的（「世界・ドメインの知識」） |
| 例 | バグ修正手順、設計判断、ユーザー嗜好、コマンド | 概念・用語・人物・組織・製品、ソース、横断的な合成 |
| 形態 | 短い・エージェント内部向け | 人間が Obsidian で閲覧可能な知識ページ |
| 寿命 | 揮発しやすい運用知 | 長期に積み上がる知識資産 |

- ブリッジ: ltm に保存した内容が**ドメイン知識**だった場合は wiki ingest を提案／自動連携する（逆も同様）。
- これだけで「保存反射が全部 ltm に行く」RC2 を緩和できる。

### Phase 1 — コールドスタートを断つ

1. **シード投入**: 既存 Obsidian Vault と ltm の意味記憶を一度だけ一括 ingest し、Wiki を非空状態で開始する。
2. **検索の強化（RC5）**: トークン化＋複数語 AND/OR、title・`aliases`・frontmatter を重み付け検索、日本語向け正規化、ヒット 0 時も list-pages を近傍順に提示。`aliases:` frontmatter を導入し「注意機構／attention／アテンション」を相互解決する。実装方針（自前強化 vs qmd / obsidian-cli 等の外部ツール）の比較は [§3.5](#35-検索強化の実装方針比較自前強化-vs-外部-markdown-検索ツール) を参照。
3. **空振りを生産的にする**: 検索が当たらなかったら、直前に参照したソースの ingest を自動提案する（miss → grow の転換）。

### Phase 2 — Organic capture（明示コマンド無しで育てる）

1. **自律 ingest トリガーを追加**（ltm の save トリガーに相当、ただし**外部・ドメイン知識に限定**）: Web リサーチ後／ドキュメント・論文・URL を精読した後／横断的な合成回答を出した後。これを **SKILL.md ではなく `common.instructions.md`（常時適用層）に配線**する（RC4 対策）。
2. **軽量な `raw/` 捕捉を追加**（RC6）: ingest 時にソースを `wiki_root/raw/` に保全し、Wiki を自己完結・監査可能・再合成可能・重複検出可能にする（原典準拠）。
3. **セッション終了時の sweep**: ltm の consolidation に加え、そのセッションで生まれた ingest 候補を洗い出す。

### Phase 3 — プロアクティブ配線の是正（RC4）

1. 「回答前に wiki を検索／調査のファーストチョイス」を **SKILL.md から `common.instructions.md` の早い位置に移し**、設定時は無条件・断定的なステップにし、ltm recall と並べて明確なルーティング規則を添える。
2. SessionStart / UserPromptSubmit フックで一行リマインダを注入し、スキル未ロードでも発火するようにする（要 hook 対応の検討）。

### Phase 4 — 価値の可視化とフィードバック

1. `wiki_query.py stats` ／ 成長レポート（ページ数・リンク数・直近 ingest・query ヒット率）。
2. 回答時に「Wiki から回答」を明示し、効果を体感させる。
3. query のヒット／ミスを記録し、検索チューニングと「埋めるべき空白」の発見に使う。
4. `auto_update.py` の定期実行に lint＋成長サマリを組み込む。

### Phase 5 — 摩擦低減と人間 UI

- Obsidian を人間側の閲覧・編集 UI として正式に推奨（既にサポート済み）。
- 将来的に MCP / 共通エントリ化で「捕捉も参照も 1 ステップ」を目指す（agent-wiki / Link の方向）。

---

## 3.5 検索強化の実装方針比較（自前強化 vs 外部 Markdown 検索ツール）

Phase 1-2 の「検索の強化（RC5）」をどう実装するか。外部 Markdown 検索ツール（**qmd** / **obsidian-cli 系**）の導入を含めて比較する。

### 前提となる設計制約（本リポジトリのスキル群）

- **ゼロ依存**: `ltm-use` 等は「MCP サーバーや特定エージェント専用機能を使わず、Markdown の読み書きだけで動作」を明言している。
- **クロスエージェント**: Copilot / Claude / Codex / Kiro で共通動作（`install.py` ベース、`{agent_home}` 解決）。
- **クロスプラットフォーム**: Windows 対応あり（`USERPROFILE` 解決など）。
- **ヘッドレス**: CI / Web セッション等、GUI の無い環境でも動く必要がある。

→ 外部ツールはこの 4 制約をどれだけ満たせるかで評価する。

### 候補

| 候補 | 概要 | 検索方式 | 主な依存・前提 |
|------|------|----------|----------------|
| **A. 自前強化（stdlib のみ）** | `wiki_query.py` をトークン化・別名・重み付け・近傍提示に拡張 | キーワード／TF-IDF 程度 | なし（Python 標準ライブラリ） |
| **B. qmd**（[tobi/qmd](https://github.com/tobi/qmd)） | ローカル CLI 検索エンジン。BM25＋ベクトル＋LLM リランク、MCP・JSON 出力 | ハイブリッド（最高品質） | **Node.js≥22 / Bun**、GGUF モデル計 ~2GB 自動DL、SQLite、（実質）GPU |
| **C. obsidian-cli 系**（[Yakitrak/notesmd-cli](https://github.com/Yakitrak/notesmd-cli) 等） | Vault をターミナル操作。grep 風／JSON 出力の検索 | キーワード（grep 相当） | Go バイナリ等の別途インストール。ツールにより Vault 前提 |
| **D. 公式 obsidian CLI**（既存 `obsidian-use` が統合済み） | 実行中 Obsidian を操作 | アプリ内検索 | **Obsidian アプリが起動している必要がある** |

### 制約への適合

| 制約 | A 自前 | B qmd | C obsidian-cli 系 | D 公式 obsidian |
|------|:--:|:--:|:--:|:--:|
| ゼロ依存 | ✓ | ✗（Node＋2GBモデル） | △（要バイナリ） | ✗ |
| クロスエージェント | ✓ | △（MCP対応エージェント寄り） | ○ | ○ |
| クロスプラットフォーム | ✓ | △（GPU/モデル差） | ○ | △ |
| ヘッドレス動作 | ✓ | ○ | ○ | **✗（GUI必須）** |
| 検索品質 | △〜○ | **◎（意味検索）** | ○ | ○ |
| 日本語・表記ゆれ | △（要実装） | **◎（多言語埋め込み）** | △ | ○ |
| 導入摩擦 | **最小** | 大 | 中 | 中（Obsidian常駐） |

### 検討の結論

- **既定路線は A（自前強化）**。スキル群の「ゼロ依存・クロス環境・ヘッドレス」という根幹の設計制約を唯一すべて満たす。RC5 の主因（単一キーワードの部分一致）は、トークン化・別名（`aliases`）・frontmatter 重み付け・近傍提示という**軽量な改良で大半が解消**でき、コールドスタート（RC1）への即効性も高い。
- **D（公式 obsidian CLI）は wiki 検索のバックエンドには不適**。GUI 常駐が必須でヘッドレス要件に反する。ただし `obsidian-use` が担う**人間側の閲覧・編集 UI** としては既に有効で、役割が違う（competing ではなく complementary）。
- **B（qmd）は「将来のオプトイン強化」として位置づける**。意味検索・多言語・MCP は本質的に魅力的で、Wiki が大規模化し純テキスト検索が頭打ちになった段階で価値が出る。ただし Node＋約2GBのモデル＋（実質）GPU は本スキルの既定依存にはできない。**導入するなら「検索バックエンドの差し替え可能化（pluggable backend）」として、qmd があれば使い、無ければ自前にフォールバックする設計**にする。
- **C（obsidian-cli 系）は中間案**だが、検索品質は grep 相当で A から大きく前進せず、別バイナリ依存が増えるだけなので**優先度は低い**。

### 推奨アーキテクチャ（pluggable search backend）

```
wiki_query.py search "<kw>"
   ├─ backend=builtin（既定・ゼロ依存）── トークン化＋別名＋重み付け＋近傍提示
   └─ backend=qmd（任意・検出時）──────── qmd query --json をラップして利用
```

- 既定は builtin。`skill_configs.wiki-use.search_backend: "qmd"` かつ `qmd` が PATH にあるときのみ B を使う。
- 出力フォーマット（ヒットページ・スニペット）を共通化し、上位の ingest/query フローからはバックエンドを透過にする。
- これにより「まず A で RC5 を潰す → 必要な人だけ B にオプトイン」という**段階導入**が可能になり、ゼロ依存の既定値を壊さない。

### 実装順序への反映

1. **今回**: A（builtin 強化）を実装し RC5 を解消する。
2. **後続（任意）**: search backend を抽象化し、qmd アダプタをオプトインで追加する。

---

## 4. 優先順位とロードマップ

| 優先 | 施策 | 効果 | コスト |
|------|------|------|--------|
| ★★★ | Phase 0 役割線引き＋ Phase 1-2 検索強化 | RC2/RC5 を即時緩和、空振り激減 | 低 |
| ★★★ | Phase 2-1 自律 ingest を common.instructions に配線 | organic capture が始まる | 中 |
| ★★ | Phase 1-1 シード投入 | コールドスタート解消 | 低（一度きり） |
| ★★ | Phase 3 プロアクティブ配線是正 | query が実際に発火する | 中 |
| ★ | Phase 2-2 `raw/` 捕捉 | compounding・重複検出 | 中 |
| ★ | Phase 4 可視化 | 定着・実感 | 中 |

最小で効くのは **Phase 0 ＋ Phase 1（検索強化）＋ Phase 2-1（common.instructions への配線）** の 3 点セット。

---

## 5. 成功指標（KPI）

- **organic ingest 率**: Obsidian 一括以外に由来する ingest イベント数 / 週（現状ほぼ 0 → 目標 > 0 を継続）。
- **query ヒット率**: 検索が関連ページを返した割合（検索強化前後で比較）。
- **wiki 引用率**: エージェントの回答のうち wiki ページを引用した割合。
- **成長量**: ページ数・リンク数の週次増加。
- **二重管理の解消**: ltm と wiki に同一知識が重複保存される件数（境界定義後に減少）。

---

## 6. 次アクション

1. 本ドキュメントのレビューと Phase 0 の役割定義の合意。
2. `wiki_query.py search` の強化（トークン化・別名・近傍提示）を最初の実装 PR とする。
3. `common.instructions.md` に「ドメイン知識は wiki / 運用知は ltm」のルーティングと自律 ingest トリガーを追記。
4. シード投入を一度実行してコールドスタートを抜ける。
