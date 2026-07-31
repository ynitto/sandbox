# スキルポートフォリオレビューと新規スキル提案（2026-06-09）

全 71 スキルの SKILL.md・メタデータ・README・過去の提案ドキュメント（2026-03-08 / 03-10 / 04-04）を横断レビューした結果。
過去提案（security-auditor・db-schema-designer・i18n-localizer・accessibility-auditor 等）と重複する案は除外し、**現状の実態から観測できた問題**と**新しい切り口の案**に絞る。

## 0. サマリ

| 種別 | 件数 | 最優先 |
|------|------|--------|
| 既存スキル・運用の改善 | 7 | eval.json バックフィル / トリガー衝突解消 |
| 新規スキル案 | 8 | skill-trust-scanner / doc-drift-detector |

---

## 1. 既存スキル・運用の改善提案

### 1.1 eval.json / meta.yaml のバックフィル — 優先度: 高

**現状（実測）**:
- `eval.json`（発動トリガー評価フィクスチャ）を持つスキル: **2 / 71**（gitlab-idd・windows-app-automation のみ）
- `meta.yaml`（io_contract）を持つスキル: **11 / 71**

skill-evaluator・empirical-prompt-tuning・git-skill-manager evaluate というスキル品質のライフサイクル機構が揃っているのに、評価の土台となるフィクスチャがほぼ存在しない。gitlab-idd の eval.json（should_trigger 真偽 16 ケース）は良いお手本になっており、これを横展開するだけで効果が大きい。

**提案**:
1. 全スキルに eval.json を整備する。description の発動フレーズから機械的に下書きを生成できるため、skill-creator に「eval.json 自動生成」ステップを追加して新規作成時の必須成果物にする
2. 既存 71 スキルへのバックフィルは scrum-master のバックログ向きのタスク（1 スキルあたり 10〜20 ケース、独立並列実行可能）
3. meta.yaml は全部に書く必要はないが、オーケストレーター（skill-mentor / scrum-master）から委譲されるスキルには io_contract を必須化する

### 1.2 トリガーフレーズ衝突の解消 — 優先度: 高

**現状（実測した衝突例）**:

| フレーズ | 衝突しているスキル |
|---------|------------------|
| 「仕様書を作って」 | doc-coauthoring と code-to-specs の**両方の description に同一フレーズ** |
| 「テストを追加して」 | bruno-e2e-builder の description に含まれるが、汎用すぎて TDD・webapp-testing 等の文脈でも発火しうる |
| 「リバースエンジニアリングして」 | domain-modeler（逆引きモード）と code-to-specs |
| 「CHANGELOGを作って」 | commit-pr-writer と git-skill-manager（変更履歴生成） |
| 「レビューして」 | agent-reviewer・self-checking・sprint-reviewer・skill-evaluator（境界記述はあるが排他条件が片側にしかない） |

**提案**:
1. 各 description の発動フレーズを「そのスキルでしか成立しない識別的フレーズ」に絞る。汎用フレーズ（「テストを追加して」「仕様書を作って」）は前提条件付きに書き換える（例: bruno-e2e-builder →「OpenAPI 由来の .bru テストを追加して」）
2. eval.json の should_trigger: false ケースに「隣接スキルの代表フレーズ」を必ず含める規約にする（gitlab-idd は既にこのパターンを実践している）
3. 衝突の機械検出は後述 2.2 skills-lint に委ねる

### 1.3 description のコンテキスト予算管理 — 優先度: 中

71 スキルの description は常時エージェントのコンテキストに載る。現状、description に発動フレーズを 10 個以上列挙するスキルが多数あり（code-simplifier・systematic-debugging・requirements-definer 等）、1 スキルで 300〜500 字を消費している。

**提案**: description は「1 文の要約 + 識別的トリガー 4〜6 個 + 境界（使わない場合）」の 3 要素・250 字以内を目安に統一する。詳細なフレーズバリエーションは eval.json 側に移す（評価には使えるがコンテキストは消費しない）。

### 1.4 大型 SKILL.md の progressive disclosure — 優先度: 中

**現状（実測）**: 300 行超が 16 スキル。特に domain-modeler は **529 行**（references/ を持っているのに本文も肥大）、doc-coauthoring は **407 行で references/ なし**。

**提案**: 500 行を上限、300 行を警告ラインとし、手順の詳細・テンプレート・出力例は references/ へ移す。対象上位: domain-modeler(529) / windows-app-automation(461) / git-skill-manager(460) / patent-writer(455) / systematic-debugging(433) / doc-coauthoring(407)。

### 1.5 table-spec-extractor のアーカイブ実施 — 優先度: 中

description で【非推奨】を明示し後継（spec-value-finder）も案内済みだが、Neo4j/GPU 依存のパイプライン一式（scripts・tests）がリポジトリに残ったまま。git-skill-manager に archive 操作が定義されているのに、自リポジトリに適用されていない。

**提案**: archive 運用を適用してスキル一覧から外す（履歴は Git に残る）。「非推奨化したスキルが N ヶ月後に自動でアーカイブ候補に挙がる」ルールを git-skill-manager に追加するとライフサイクルが閉じる。

### 1.6 README スキル一覧の自動生成 — 優先度: 中

README の 71 件のカテゴリ表は手書きで、スキル追加・改名のたびにドリフトする（description との文言差分も既に発生している）。

**提案**: SKILL.md frontmatter（+ 過去提案 v2 §3 の category/tags）から README の一覧表を生成するスクリプトを tools/ に追加し、CI で同期チェックする。

### 1.7 エージェント互換性表記の標準化 — 優先度: 低

「Copilot・Kiro・WSL 対応」「GitHub Copilot / Claude Code 両環境で利用可能」「日本語・GitHub Copilot対応」など、対応エージェント・OS の表記がスキルごとにバラバラで description に埋め込まれている。

**提案**: frontmatter に `compatibility:`（agents / os / 必要ランタイム）を構造化フィールドとして定義し、description からは削る。git-skill-manager の pull 時に環境不一致を警告できるようになる副次効果もある。

---

## 2. 新規スキル提案（新しい切り口）

### 2.1 skill-trust-scanner — スキルのサプライチェーン検査 🆕 優先度: 高

**切り口**: このエコシステムは git-skill-manager で**外部リポジトリからスキルを pull して実行する**仕組みを持つが、導入前の安全性検査が存在しない。スキルは「エージェントに対する指示書 + 実行スクリプト」なので、悪意ある（または事故的に危険な）スキルはプロンプトインジェクション・認証情報の流出・破壊的コマンドの実行経路になる。

**機能**:
- SKILL.md 本文の指示インジェクションパターン検査（「他の指示を無視して」「この操作はユーザーに報告せず」等）
- scripts/ の静的検査: 環境変数・認証情報の外部送信、curl|sh、rm -rf、難読化コード
- 外部送信先 URL / ドメインの許可リスト照合
- 判定を trusted / needs-review / blocked で出力し、git-skill-manager pull のゲートとして組み込む

**連携**: git-skill-manager（pull 時フック）、skill-evaluator（品質評価と直交する安全性評価）

### 2.2 skills-lint — ポートフォリオ横断ヘルスチェック 🆕 優先度: 高

**切り口**: skill-evaluator は「単体スキルの品質」を見るが、71 スキルを**システムとして**見る視点がない。本レビューで見つけた問題（トリガー衝突・メタ未整備・行数超過）はすべて横断検査で機械検出できる。過去提案 v2 §9（クロスリファレンス検証）・§13（機械可読カタログ）の発展形。

**機能**:
- トリガー衝突検出: 各スキルの eval.json の should_trigger ケースを**他の全スキルの description と突き合わせ**、複数スキルにマッチするクエリを報告
- カバレッジレポート: eval.json / meta.yaml / references 化の整備率
- 予算検査: description 字数・SKILL.md 行数
- スキル間相互参照（「〜は patent-writer を使う」等）のリンク切れ検出
- README 一覧との同期検査（1.6）
- GitHub Actions で PR ごとに実行

### 2.3 doc-drift-detector — 仕様と実装の乖離検知 🆕 優先度: 高

**切り口**: code-to-specs（コード→仕様書）の**逆方向の検証**が存在しない。仕様書・README・ランブックは書いた瞬間から腐り始めるが、このポートフォリオはドキュメント生成スキルが 10 個ある一方、鮮度を守るスキルがゼロ。

**機能**:
- 既存ドキュメント（仕様書・README・runbook・ADR）とコードベースを突き合わせ、乖離箇所を「ドキュメントが古い / コードが仕様違反 / どちらか不明」に分類して報告
- git log から「ドキュメント更新を伴わない大きな実装変更」を検出して疑い箇所を絞り込み
- 修正パッチ案の生成（code-to-specs / technical-writer へ委譲）

**連携**: code-to-specs・runbook-author・technical-writer・agent-reviewer（document 観点）

### 2.4 threat-modeler — 設計フェーズの脅威モデリング 🆕 優先度: 中

**切り口**: 過去提案の security-auditor は「実装済みコードの監査」。その**上流**、設計段階で攻撃面を潰す脅威モデリング（STRIDE・攻撃ツリー・信頼境界図）が空白。failure-driven-development が「障害」を先に設計するのと対になる「攻撃」を先に設計するスキル。

**機能**: domain-modeler / api-designer / aws-architecture-diagram の成果物を入力に、信頼境界の特定 → STRIDE 分析 → リスク評価（DREAD）→ 緩和策の設計反映。Mermaid でデータフロー図 + 信頼境界を出力。

### 2.5 test-data-generator — テストデータ生成 🆕 優先度: 中

**切り口**: TDD・bruno-e2e-builder・webapp-testing とテスト実行系は厚いが、**テストデータを作る**スキルがない。現実的なフィクスチャ作りはテスト工数の大きな割合を占める。

**機能**:
- スキーマ（OpenAPI / DDL / 型定義）から現実的なシード・フィクスチャ生成（Faker 系）
- 境界値・異常系・ユニコード/タイムゾーン地雷などのエッジケースセット生成
- 本番類似データの匿名化生成（privacy-compliance と連携し PII を合成データに置換）

### 2.6 load-test-designer — 負荷テスト設計 🆕 優先度: 中

**切り口**: slo-designer が目標を決め、performance-profiler がコードを掘り、observability-designer が計測するが、「**SLO を負荷で検証する**」中間が欠けている。

**機能**: OpenAPI / ユーザーシナリオから k6 / Locust スクリプトを生成し、SLO 由来の合否しきい値（p99 レイテンシ・エラー率）を埋め込む。ramp-up / spike / soak のシナリオパターン選択、結果の SLO 照合レポート。

**連携**: slo-designer（しきい値の供給元）・bruno-e2e-builder（機能テストとの対）・ci-cd-configurator（パイプライン組み込み）

### 2.7 cloud-cost-estimator — クラウドコスト試算・最適化 🆕 優先度: 中

**切り口**: estimation は「工数・人日」の見積もりで、**ランニングコスト**の軸がない。ポートフォリオは AWS 専用と明言するスキルが複数あり（ci-cd-configurator・aws-architecture-diagram・dynamodb-designer）、コスト軸の需要は高い。

**機能**: アーキテクチャ図（Draw.io XML）・IaC・構成記述からサービス別の月額試算レンジを算出。DynamoDB のキャパシティモード選定・NAT/データ転送の地雷・サーバーレス vs 常駐の損益分岐など最適化提案。aws-architecture-diagram の出力をそのまま入力にできるのが独自の強み。

### 2.8 session-retrospective — エージェントセッションの振り返り 🆕 優先度: 低

**切り口**: sprint-reviewer は scrum-master 専用の振り返り。**スプリント外の通常セッション**から教訓を抽出して ltm-use / persona-use / empirical-prompt-tuning に流す自動フィードバックループがない。

**機能**: セッショントランスクリプトを分析し、(a) 手戻り・誤解・ツール失敗のパターン抽出、(b) ltm-use への知見保存、(c) スキルへのフィードバック記録（git-skill-manager の verdict）の下書き、(d) 繰り返し失敗があれば empirical-prompt-tuning の起動提案。

---

## 3. 優先度マトリクス

| 施策 | 効果 | コスト | 優先度 |
|------|------|--------|--------|
| 1.1 eval.json バックフィル | 高（評価基盤が機能し始める） | 低（並列化可能） | **P0** |
| 1.2 トリガー衝突解消 | 高（誤発動の直接削減） | 低 | **P0** |
| 2.2 skills-lint | 高（1.2/1.3/1.4/1.6 を恒久化） | 中 | **P1** |
| 2.1 skill-trust-scanner | 高（pull 運用の前提安全性） | 中 | **P1** |
| 2.3 doc-drift-detector | 高（ドキュメント資産の保全） | 中 | **P1** |
| 1.4 progressive disclosure | 中 | 低 | P2 |
| 1.5 table-spec-extractor アーカイブ | 中 | 低 | P2 |
| 2.4〜2.7 新スキル群 | 中 | 中 | P2 |
| 1.3 / 1.6 / 1.7 / 2.8 | 中〜低 | 低〜中 | P3 |

**推奨着手順**: 1.2（衝突する description の文言修正、即日可能）→ 1.1（eval.json バックフィルを scrum-master で並列実行）→ 2.2 skills-lint で再発防止を CI に固定 → 2.1 / 2.3 を次の新規スキルとして skill-creator で作成。
