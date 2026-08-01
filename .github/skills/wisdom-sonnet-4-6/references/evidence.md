# wisdom-sonnet-4.6 の研究的根拠

本スキルの各規律がどの実測・研究・公式ガイダンスに基づくかの一覧。
規律を追加・変更するときは、ここに根拠を追記して整合を保つこと。
（調査時点: 2026-07。製品仕様は公式資料を優先する）

## 目次

- [1. 能力差は一様ではなく集中している](#1-能力差は一様ではなく集中している)
- [2. 実務者が報告するSonnetの失敗モード](#2-実務者が報告するsonnetの失敗モード)
- [3. Opus5 / Fable5 で追加してシミュレートする回路](#3-opus5--fable5-で追加してシミュレートする回路)
- [4. 採用した技法と実証値](#4-採用した技法と実証値)
- [5. Kiro CLIでの独立候補生成](#5-kiro-cliでの独立候補生成)
- [6. 意図的に採用しなかったもの](#6-意図的に採用しなかったもの)
- [7. 残ギャップ](#7-残ギャップ)
- [8. バージョン履歴と統合方針](#8-バージョン履歴と統合方針)
- [9. 主要出典](#9-主要出典)

## 1. 能力差は一様ではなく集中している

| ベンチマーク | Sonnet 4.6 | Opus 4.6 | 差 |
|---|---|---|---|
| SWE-bench Verified（仕様明確なコーディング） | 79.6% | 80.8% | 約1.2pt |
| OSWorld-Verified（コンピュータ操作） | 72.5% | 72.7% | 約0.2pt |
| Terminal-Bench 2.0（長時間エージェント作業） | 約59% | 65.4% | 約6pt |
| GPQA Diamond（専門的多段推論） | 74.1% | 91.3% | 約17pt |
| ARC-AGI-2（新規抽象推論・高effort） | 約60.4% | 約69% | 約8pt |
| MRCR v2（長文多ファイル一貫性） | 相対劣位 | 相対優位 | 実務差が大 |
| GDPval-AA（オフィス系知的労働・Elo） | 1633 | 1606 | Sonnet勝ち |

- 出典: Vellum、webscraft.org、claude5.ai、MarkTechPost、ZBuild 等の比較
  （サードパーティ集計のため数値は中確度。複数ソースで一致するものを採用）
- 含意: 「全般的に賢くする」のではなく、差が集中する領域
  （深い推論・新規問題・長期作業・長文一貫性）を狙い撃ちする。

## 2. 実務者が報告するSonnetの失敗モード

- 「Opus 4.6が優るのは、長い地平の計画、複数フェーズ、注意深い状態追跡、
  **いつやめるかの判断**が要るタスク」「Sonnetが限界に達している兆候は、
  繰り返しの修正、浅いデバッグ、複数ファイル認識の弱さ、長いツールループ、
  **もっともらしいが実際の制約を外している結果**」
  — AC Digest "What Opus 4.6 Actually Changes for Practitioners"
- 「Opus 4.6は可能な根本原因についてより明示的に推論する傾向があり、
  是正措置を勧める前に**仮説を列挙することが多い**」。Sonnetは根本原因が
  複数ファイルにまたがるカスケードだと劣る — zoer.ai の比較記事
- 文脈が30K超の多ファイル作業で、Opusの一貫性が実務上はっきりする
  — ZBuild の比較
- Sonnet 4.5系のシステムカード: 不可能タスクでのreward hacking率53%
  （テストへのハードコード等）。4.6で改善したが対策プロンプトは公式に現役
- Sonnet 4.5/4.6はコンテキスト残量を自己認識し、**残量が近づくと自然に
  まとめに入る**ことをAnthropicが公式に文書化（対策プロンプトも公式提供）

## 3. Opus5 / Fable5 で追加してシミュレートする回路

上位モデルは次を比較的ネイティブに行う。Sonnet 4.6では明示スキャフォールドが必要。

| 上位モデル挙動 | 公式・観測 | 本スキルでの代替 |
|---|---|---|
| Opus5の自己検証 | Opus5は検証指示なしでも自己検証し、過剰な検証指示は害 | Sonnetには外部検査ゲートと隔離審査を残す（ここは意図的に逆） |
| Opus5のwriter-verifier協調 | マルチエージェントで上書きが少ない | 候補生成と審査をラウンド分離、編集は親のみ |
| Fable5の長期自律 | 何時間〜何日規模の目標志向ラン | フェーズ計画＋Working Memory＋停止条件 |
| Fable5の進捗接地 | ツール結果監査で虚偽進捗がほぼ消える | 「完了」は受入検査の証拠必須 |
| Fable5の記憶 | レッスンノートを外部ファイルへ | Working Memory / レッスン欄 |
| Fable5の新鮮コンテキスト検証 | 自己批判より隔離verifierが強い | 匿名化敵対審査・検証専用subagent |
| Fable5/Opus5の曖昧さ航行 | 複線リクエストから次手を決められる | 問題リフレーム・制約衝突表 |
| Opus5の幻覚耐性は完全ではない | 不確実時に応答しすぎる報告あり | 確度校正と未検証の明示を強化 |

重要: Opus5向けに「検証指示を外せ」という公式助言は、**Opus5本体**向けである。
本スキルの実行主体はSonnet 4.6なので、検証スキャフォールドは外さない。

## 4. 採用した技法と実証値

| 規律（本スキルの柱） | 技法 | 実証 |
|---|---|---|
| 推論深度 | 「think thoroughly」系の一般指示＋原子命題分解 | Sonnet 4.6はARC-AGI-2で高effort時にOpusへ接近。Anthropic「一般的指示は手書き手順より良い推論を生むことが多い」 |
| 計画先行 | Plan-and-Solve (Wang et al., ACL 2023) | GSM8Kで欠落ステップ誤り12%→7% |
| 仮説列挙 | Anthropic公式リサーチプロンプト | 公式推奨＋実務者観察 |
| 軽量自己一貫性 | Self-Consistency (Wang et al., ICLR 2023) | GSM8Kで+17.9pt。離散判断は少数経路でもゲインの大半 |
| 複数案比較 | Tree of Thoughts の軽量変形 | Game of 24で74% vs 4%。分岐評価の弱さ対策で2〜4案に縮約 |
| 外部検証ループ | Reflexion (Shinn et al., NeurIPS 2023) | HumanEval 91% pass@1（ベース80%）。外部フィードバック接地が条件 |
| 自己改善の上限 | Self-Refine (Madaan et al., NeurIPS 2023) | 平均+20pt、数周で飽和 → 1周改稿 |
| 抽象的自己修正の禁止 | Huang et al., ICLR 2024 | 外部シグナルなしの自己修正は性能を悪化させ得る |
| 敵対的レビュー | Anthropic評価者-最適化者 | 書いた直後の本人は甘い。指摘対象は正しさ・要件に限定 |
| 網羅列挙→後段フィルタ | Anthropic移行ガイド | 先に重要度フィルタすると本物の問題が消える |
| 状態外部化 | Anthropic公式＋Fable5 memory guidance | Opus/Fableの内部状態追跡の代替 |
| 早期切り上げ禁止 | Anthropic公式プロンプト | トークン残量理由の早期終了を禁止 |
| 反対結論の鋼人化 | Debate系＋批判的思考の実務ヒューリスティック | 確証バイアス緩和 |
| 引用先出し | 長文MRCRギャップへの実務対策 | 要約幻覚の抑制 |

## 5. Kiro CLIでの独立候補生成

Kiro CLIは、独立したコンテキストを持つサブエージェントを同時に最大4件
実行できる。親コンテキストを候補生成の全履歴で汚さず、先行案を見せない
独立生成が可能である。

ただし運用実態として、Kiroのサブエージェントはしばしばハングし不安定である
（本リポジトリ利用者の実地観測）。このためv4では、単一コンテキストでの
逐次生成（各案の前にbriefだけを読み直して視点を切り替える）を主経路とし、
サブエージェントは「安定して使える場合に限る任意の加速手段」へ降格した。
無応答・エラー時は1回だけ再試行して即縮退し、復帰待ちでタスクを
ブロックしない。縮退時は独立検証でないことを明記する。

- Kiro CLI Agent Skills: https://kiro.dev/docs/cli/skills/
- Kiro CLI Subagents: https://kiro.dev/docs/cli/chat/subagents/
- Kiro CLI Built-in tools: https://kiro.dev/docs/cli/reference/built-in-tools/
- Kiro CLI Custom agent configuration:
  https://kiro.dev/docs/cli/custom-agents/configuration-reference/

同じSonnet 4.6を複数回使う結果は相関しており、多数決は真偽判定にならない。
3〜4候補は探索幅の確保にだけ使い、別ラウンドの反証と外部検証を判定に使う。

## 6. 意図的に採用しなかったもの

- **多数決型のサブエージェント合議**: 同一モデルの誤りは相関するため不採用。
- **強圧的スキャフォールディング**（CRITICAL/MUST連呼）: 4.6世代は過剰発動しやすい。
- **APIレベルのSelf-Consistency**: temperature制御ができないため経路照合に縮約。
- **フルのTree of Thoughts**: 外部ハーネス前提かつ分岐評価が弱いため軽量変形のみ。
- **judge-of-judges / 無限自己改善**: コストばかり増え、外部証拠なしでは劣化し得る。
- **内部思考の逐語再現要求**: Fable5では reasoning extraction 拒否の対象にもなり得る。
  本スキルは検証可能な判断記録だけを要求する。
- **Opus5向けの検証指示削除**: 実行主体がSonnetのため不採用（上記セクション3）。

## 7. 残ギャップ

1. **専門ドメインの深い多段推論**（GPQA型）— 分解と検証で縮むが消えない。
2. **超長文からの正確な検索・想起** — 引用先出しで緩和。必要なら分割探索。
3. **第一原理の新規抽象**（ARC系）— 独立候補と鋼人化で幅は出るが天井は残る。
4. **ネイティブ長期記憶と高effort思考** — 外部メモリと工程で近似するだけ。
5. エスカレーション目安: 逐次推論>3段、複数ディレクトリ横断、新規アルゴリズム、
   自己申告確度が低い、またはDEEPでも残リスクがCritical。

## 8. バージョン履歴と統合方針

- **v1**: 検証駆動コア（4本柱＋誠実性）。単一コンテキスト前提。
- **v2（ynitto）**: Kiro CLI統合の明確化、サブエージェント運用の導入。
- **v3（Cursor Agent / Grok視点）**: ULTRAティア、認知プロトコル集
  （問題リフレーム・原子命題・双方向推論・鋼人化・引用先出し）、Fable風
  作業メモリ、候補D（制約逆算）、二重レンズ審査、事前検死、タスク別
  プレイブックとサブエージェント定型文を追加。
- **v4（統合）**: 各LLMの追加内容を保持したまま、実行主体のSonnet 4.6が
  無理なく追従できる形へ再構成した。
  1. **単一コンテキスト逐次を主経路化**: サブエージェント前提の工程を
     「安定時のみの任意手段＋即縮退規則」へ変更（上記セクション5）。
  2. **ティアを4→3へ統合**: ULTRAをDEEPへ吸収し、重い道具（作業メモリ・
     事前検死・二重レンズ・候補D）はティアフラグでなく発動条件
     （長時間作業・高失敗コスト・複数候補の接戦）で適用する。
     ティア別条件分岐の追跡負荷を減らすため。
  3. **参照を4ファイル→2ファイルへ集約**: cognitive-protocols /
     task-playbooks / subagent-prompts を toolkit.md へ統合。Kiroの参照
     解決の不確実さと読み分けコストを減らし、頻用プロトコル（リフレーム・
     引用先出し・鋼人化・事前検死・Working Memory）はSKILL.md本文へ内蔵。

これらは「人格ロールプレイで賢く見せる」のではなく、失敗モードに対する
観測可能な工程追加である。

## 9. 主要出典

- Anthropic Prompting best practices:
  platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-4-best-practices
- Prompting Claude Fable 5:
  platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5
- Prompting Claude Opus 5:
  platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5
- Claude Code ベストプラクティス: code.claude.com/docs/en/best-practices
- Building Effective Agents: anthropic.com/research/building-effective-agents
- Self-Consistency: arxiv.org/abs/2203.11171 ／ Reflexion: arxiv.org/abs/2303.11366
- Self-Refine: arxiv.org/abs/2303.17651 ／ 自己修正の限界: arxiv.org/abs/2310.01798
- Tree of Thoughts: arxiv.org/abs/2305.10601 ／ 生成と判別の非対称: arxiv.org/pdf/2410.17820
- Plan-and-Solve: aclanthology.org/2023.acl-long.147 ／ Debate: arxiv.org/abs/2305.14325
- 実務者比較: acdigest.substack.com, zoer.ai, vellum.ai, zbuild.io
