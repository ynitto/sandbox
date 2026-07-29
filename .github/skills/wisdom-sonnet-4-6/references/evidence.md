# wisdom-sonnet-4.6 の研究的根拠

本スキルの各規律がどの実測・研究・公式ガイダンスに基づくかの一覧。
規律を追加・変更するときは、ここに根拠を追記して整合を保つこと。
（調査時点: 2026-07。出典の一部はプロキシ制限下で検索エンジン要約経由の確認）

## 1. 能力差は一様ではなく4領域に集中している

| ベンチマーク | Sonnet 4.6 | Opus 4.6 | 差 |
|---|---|---|---|
| SWE-bench Verified（仕様明確なコーディング） | 79.6% | 80.8% | 約1.2pt |
| OSWorld-Verified（コンピュータ操作） | 72.5% | 72.7% | 約0.2pt |
| Terminal-Bench 2.0（長時間エージェント作業） | 約59% | 65.4% | 約6pt |
| GPQA Diamond（専門的多段推論） | 74.1% | 91.3% | 約17pt |
| ARC-AGI-2（新規抽象推論・高effort） | 約60.4% | 約69% | 約8pt |
| GDPval-AA（オフィス系知的労働・Elo） | 1633 | 1606 | Sonnet勝ち |

- 出典: Vellum（vellum.ai/blog/claude-opus-4-6-benchmarks）、webscraft.org、
  claude5.ai、MarkTechPost の比較記事（サードパーティ集計のため数値は中確度。
  複数ソースで一致するものを採用）
- 含意: 「全般的に賢くする」のではなく、差が集中する領域
  （深い推論・新規問題・長期作業の判断）を狙い撃ちする設計にした。

## 2. 実務者が報告するSonnetの失敗モード

- 「Opus 4.6が優るのは、長い地平の計画、複数フェーズ、注意深い状態追跡、
  **いつやめるかの判断**が要るタスク」「Sonnetが限界に達している兆候は、
  繰り返しの修正、浅いデバッグ、複数ファイル認識の弱さ、長いツールループ、
  **もっともらしいが実際の制約を外している結果**」
  — AC Digest "What Opus 4.6 Actually Changes for Practitioners"
  (acdigest.substack.com)
- 「Opus 4.6は可能な根本原因についてより明示的に推論する傾向があり、
  是正措置を勧める前に**仮説を列挙することが多い**」。Sonnetは根本原因が
  複数ファイルにまたがるカスケードだと劣る — zoer.ai の比較記事
- Sonnet 4.5系のシステムカード: 不可能タスクでのreward hacking率53%
  （テストへのハードコード等）。4.6で改善したが対策プロンプトは公式に現役
- Sonnet 5システムカードは「sycophancyが**4.6比で**顕著に改善」と記載
  → 追従性は4.6の既知の弱点
- Sonnet 4.5/4.6はコンテキスト残量を自己認識し、**残量が近づくと自然に
  まとめに入る**ことをAnthropicが公式に文書化（対策プロンプトも公式提供）

## 3. 採用した技法と実証値

| 規律（本スキルの柱） | 技法 | 実証 |
|---|---|---|
| 柱1: 推論深度 | 思考の延長・「think thoroughly」系の一般指示 | Sonnet 4.6はARC-AGI-2で高effort+120k思考時に約60.4%となり、デフォルト設定時より大幅にOpusへ接近。Anthropic「一般的指示は手書きの手順より良い推論を生むことが多い」 |
| 柱2: 計画先行 | Plan-and-Solve (Wang et al., ACL 2023) | GSM8Kで欠落ステップ誤り12%→7%。Anthropicの推奨ワークフロー第1位「調査・計画を実装から分離」 |
| 柱2: 仮説列挙 | Anthropic公式リサーチプロンプト「競合仮説を複数立て、確度を記録し、定期的に自己批判せよ」 | 公式推奨＋実務者観察（Opusのデフォルト挙動の直接的な再現） |
| 柱2: 軽量自己一貫性 | Self-Consistency (Wang et al., ICLR 2023) | GSM8Kで+17.9pt（40サンプル時。ゲインの大半は少数サンプルで得られる）。離散判断限定・2経路照合として軽量化して採用 |
| 柱2: 複数案比較 | Tree of Thoughts (Yao et al., NeurIPS 2023) の軽量変形 | Game of 24で74% vs 4%。ただし小型モデルは分岐の判別が相対的に苦手（arXiv:2410.17820）なため、フル探索でなく「2〜3案生成→制約評価→コミット」に縮約 |
| 柱3: 外部検証ループ | Reflexion (Shinn et al., NeurIPS 2023) | HumanEval 91% pass@1（ベースライン80%）。**外部フィードバック（テスト結果等）に接地した反省のみ有効** |
| 柱3: 自己改善の上限 | Self-Refine (Madaan et al., NeurIPS 2023) | 平均+20pt。ただし数周で飽和 → 1〜2周で打ち切る規定 |
| 柱3: 抽象的自己修正の禁止 | Huang et al., ICLR 2024 "LLMs Cannot Self-Correct Reasoning Yet" | 外部シグナルなしの自己修正は**性能を悪化させる** → 「基準なしの見直し禁止」の根拠 |
| 柱3: 敵対的レビュー | Anthropic評価者-最適化者パターン | 「書いた直後の本人は自分のコードに甘い」。ただし「gapを探せと言われたレビュアーは健全でも何か報告する」ため、指摘対象を正しさ・要件に限定 |
| 柱3: 網羅列挙→後段フィルタ | Anthropic移行ガイド | 4.6世代は指示を字義通り守るため、先に重要度フィルタをかけると本物の問題が消える。「不確実でも全部挙げ、確度と深刻度を付け、フィルタは後段」が公式推奨 |
| 柱4: 状態外部化 | Anthropic公式（progress notes・tests.json・git履歴の活用） | Opusの内部状態追跡力の代替。「テストの削除・改変は容認されない」も公式文言 |
| 柱4: 早期切り上げ禁止 | Anthropic公式プロンプト | 「トークン残量を理由にタスクを早期終了するな。残量に関係なく人工的に止めるな」 |
| 誠実性: 反追従 | Multiagent Debate (Du et al., ICML 2024) からの外挿＋システムカードの弱点記載 | 前提の検証・根拠付き反論。直接の測定は薄いが妥当な外挿として採用 |
| 誠実性: 接地 | Anthropic公式 | 「開いていないコードについて推測するな。参照されたファイルは必ず読め」 |

## 4. 意図的に採用しなかったもの

- **サブエージェント並列化**（Anthropicのorchestrator-workersパターン等）:
  効果はあるが、Kiro環境でサブエージェントがハング・不安定という運用実態の
  ため全面不採用。単一コンテキスト内の役割切替で代替。
- **強圧的スキャフォールディング**（CRITICAL/MUST連呼、「迷ったらツールを
  使え」）: 4.6世代はシステムプロンプトへの追従が強く、旧世代向けの
  反怠惰プロンプトは**過剰発動を招く**とAnthropicが明記。平叙な命令形＋
  理由の説明で書く。
- **APIレベルのSelf-Consistency（多数決サンプリング）**: スキルからは
  temperature等を制御できないため、「独立2経路で解いて照合」に縮約。
- **フルのTree of Thoughts**: 外部ハーネスが必要で、かつ小型モデルは
  分岐評価が弱いため、軽量変形のみ。

## 5. 残ギャップ（プロンプトでは埋まらない領域）

1. **専門ドメインの深い多段推論**（GPQA型、差約17pt）— 思考延長で縮むが
   消えない。確度の低い結論はその旨を明示し、Opus級での再確認を推奨する
   運用でカバー。
2. **超長文からの正確な検索・想起** — Opus 4.6が明確に優る領域。引用抽出
   （関連箇所を先に引用してから推論する）で緩和。
3. エスカレーション目安（実務者ヒューリスティック）: 逐次推論>3段、
   複数ディレクトリ横断、新規アルゴリズム、自己申告確度<0.85。

## 6. 主要出典

- Anthropic プロンプトベストプラクティス:
  platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-4-best-practices
- Claude Code ベストプラクティス: code.claude.com/docs/en/best-practices
- Sonnet 4.6 発表: anthropic.com/news/claude-sonnet-4-6
- Building Effective Agents: anthropic.com/research/building-effective-agents
- Self-Consistency: arxiv.org/abs/2203.11171 ／ Reflexion: arxiv.org/abs/2303.11366
- Self-Refine: arxiv.org/abs/2303.17651 ／ 自己修正の限界: arxiv.org/abs/2310.01798
- Tree of Thoughts: arxiv.org/abs/2305.10601 ／ 生成と判別の非対称: arxiv.org/pdf/2410.17820
- Plan-and-Solve: aclanthology.org/2023.acl-long.147 ／ Debate: arxiv.org/abs/2305.14325
- 実務者比較: acdigest.substack.com, zoer.ai, vellum.ai
