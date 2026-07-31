# サブエージェントへの指示テンプレート

## 目次

- [重要な注意事項](#重要な注意事項)
- [skill-selector 呼び出し時](#skill-selector-呼び出し時)
- [スキル実行時](#スキル実行時)
- [brainstorming スキル実行時](#brainstorming-スキル実行時)
- [agent-reviewer サブエージェント起動時](#agent-reviewer-サブエージェント起動時)
- [修正リトライ時](#修正リトライ時)
- [フィードバック保存時](#フィードバック保存時)
- [スキルフィードバック記録時（git-skill-manager）](#スキルフィードバック記録時git-skill-manager)

## 重要な注意事項

- SKILL.md の内容をプロンプトに埋め込まない。ファイルパスだけ渡し、サブエージェント自身に読ませる。これによりプロンプトを短く保ち、安定性を確保する。
- すべてのテンプレートに戻り値の形式指定を含める。これにより skill-mentor が結果を確実にパースできる。
- `[角括弧]` 内は実際の値に置換すること。
- `LTM=${SKILLS_DIR}/ltm-use/scripts`（各テンプレートで共通）

---

## skill-selector 呼び出し時

Phase 2 で使用。タスク定義を渡してスキル構成を推薦してもらう。

```
skill-selector スキルでタスクに最適なスキル構成を選定する。

手順: まず skill-selector スキルの SKILL.md を読んで手順に従ってください。

タスク定義:
  ゴール: [Phase 1 で確定したゴール]
  動機: [Phase 1 で確定した動機]
  完了条件: [Phase 1 で確定した完了条件]
  制約: [Phase 1 で確定した制約]
  対象: [コード / ドキュメント / 設計 / デバッグ / 調査 / その他]

ユーザーのコンテキスト:
  [Phase 1 の壁打ちで得られた重要な情報をここに要約]

注意:
- スキルの推薦結果のみ出力してください。タスクの実行は行わないでください。
- 過剰選択を避け、タスクに最小限のスキルを選んでください。

結果を以下の形式で返してください:
selection_status: success | failure
goal: "[タスク要約]"
primary_skills:
  - name: "[skill-name]"
    role: "[このスキルが担う役割]"
supporting_skills:
  principle:
    mode: skill | fallback | none
    name: "[skill-name または null]"
    instruction: "[自然文フォールバックまたは null]"
    timing: "[before-primary / after-primary / null]"
    reason: "[推薦理由または null]"
  conditional:
    mode: skill | fallback | none
    name: "[skill-name または null]"
    instruction: "[補助指示または null]"
    timing: "[before-primary / after-primary / null]"
    reason: "[推薦理由または null]"
execution_plan:
  groups:
    - id: "A"
      after: []
      skills: ["skill-A", "skill-B"]
notes:
  - "[スキルの重複・競合がある場合]"
past_examples:
  success: []
  warnings: []
```

**skill-mentor の次の処理**: 推薦結果の構造を崩さず、`primary_skills` だけを実行対象へ変換し、`supporting_skills` / `notes` はそのまま保持して実行計画を作成する。

---

## スキル実行時

Phase 3 で使用。各タスクをスキルに委譲して実行する。

```
[skill-name] スキルで以下のタスクを実行してください。

手順: まず [skill-name] スキルの SKILL.md を読んで手順に従ってください。

## ステップ 1: 記憶参照（必須）
  python ${LTM}/recall_memory.py "[タスクキーワード]"
関連する記憶があればコンテキストに活用し、重複作業を避けること。

## ステップ 2: タスク実行
タスク:
  ゴール: [Phase 1 で確定したゴール]
  完了条件: [Phase 1 で確定した完了条件]

コンテキスト:
  [Phase 1 の壁打ちで得られたユーザーの回答・制約・好みをここに記載]

制約:
  [Phase 1 で確定した制約]

## ステップ 3: 補助指示を適用する

skill-selector が返した `supporting_skills` を以下の形式で受け取り、その内容どおりに補助フローを適用する:

supporting_skills:
  principle:
    mode: [skill | fallback | none]
    name: [skill-name または null]
    instruction: [自然文指示または null]
    timing: [before-primary | after-primary | null]
  conditional:
    mode: [skill | fallback | none]
    name: [skill-name または null]
    instruction: [自然文指示または null]
    timing: [before-primary | after-primary | null]

適用ルール:
- `timing: before-primary` の項目は、ステップ 2 に入る前の前提整理・実行方針として適用する
- `mode: skill` の項目は `${SKILLS_DIR}/[name]/SKILL.md` を探して読み、その手順を補助フローとして実行する
- `mode: fallback` の項目は `instruction` をそのまま実施する
- `mode: none` の項目はスキップする
- `timing: after-primary` の項目は、ステップ 2 の成果物に対して適用する

補助指示の適用結果（何を適用したか、何をスキップしたか）を結果フィールドに含める。

## ステップ 4: 気づきを保存（価値ある発見のみ、なければスキップ可）
  python ${LTM}/save_memory.py --non-interactive --no-dedup \
    --category [カテゴリ] --title "[知見タイトル]" --summary "[要約]" \
    --content "[詳細]" --conclusion "[学び]" --tags [タグ]

結果を以下の形式で返してください:
ステータス: 成功 / 失敗
サマリー: [1〜2文で結果を説明]
変更ファイル: [変更・作成したファイルの一覧。なければ「なし」]
成果物の種類: [ソースコード / テスト / ドキュメント / 設計 / その他]
自己評価: PASS ✅ / NEEDS_IMPROVEMENT ⚠️
補助指示の適用結果: [適用した補助スキル / フォールバック / なし]
記憶参照: [参照した記憶のID(s) またはなし]
記憶保存: [保存したファイルパス(s) またはなし]
気づき: [この実行で得た知見を1〜2文]
```

**skill-mentor の次の処理**: 結果をユーザーに報告し、すべてのスキル実行完了後に Phase 4 へ進む。

---

## brainstorming スキル実行時

Phase 3 で brainstorming スキルが選定された場合に使用。Phase 1 のタスク定義を渡し、明確化済みの質問を繰り返さないようにする。

```
brainstorming スキルで設計を深掘りしてください。

手順: まず brainstorming スキルの SKILL.md を読んで手順に従ってください。

重要: 以下のタスク定義は Phase 1 の壁打ちで既に確定済みです。
brainstorming の「明確化のための質問」フェーズでは、以下の情報で十分な項目は質問を省略し、
設計レベルの深掘り（どうやって実現するか、アプローチの比較）に集中してください。

確定済みタスク定義:
  ゴール: [Phase 1 で確定したゴール]
  動機: [Phase 1 で確定した動機]
  完了条件: [Phase 1 で確定した完了条件]
  制約: [Phase 1 で確定した制約]

ユーザーのコンテキスト:
  [Phase 1 の壁打ちで得られた全回答をここに記載]

結果を以下の形式で返してください:
ステータス: 成功 / 失敗
採用アプローチ: [ユーザーが承認したアプローチ名]
設計ドキュメント: [作成した設計ドキュメントのパス]
サマリー: [1〜2文で設計内容を要約]
```

**skill-mentor の次の処理**: 結果をユーザーに報告し、次のスキル実行へ進む。

---

## agent-reviewer サブエージェント起動時

Phase 5 Step 1 で使用。skill-mentor は perspective を決めず、成果物全体を `agent-reviewer` に渡して集約レビューを委譲する。

```
agent-reviewer スキルで以下の成果物をレビューしてください。

手順: まず agent-reviewer スキルの SKILL.md（${SKILLS_DIR}/agent-reviewer/SKILL.md）を読み、
成果物の内容から必要な perspectives を自律的に判断し、並列レビューと集約まで完了してください。

コンテキスト:
  タスクゴール: [Phase 4 のゴール]
  完了条件: [Phase 2 の完了条件]
  タスク結果サマリー: [Phase 4 の実行結果サマリー]
  変更ファイル: [Phase 4 の変更ファイル一覧。なければ「なし」]

注意:
- ユーザーへの確認・対話は行わず、レビューのみ実施すること。
- perspective は入力せず、agent-reviewer が成果物から判断すること。

結果を以下の形式で返してください:
総合レビュー結果: LGTM ✅ / Request Changes ❌
実施した perspectives: [perspective の一覧]
完了条件の充足: 満たす / 満たさない
重大な指摘件数: [N件]
主な指摘: [重大な指摘がある場合は要約。なければ「なし」]
レビュー結果JSON: [agent-reviewer の verdict-json]
```

**skill-mentor の次の処理**: agent-reviewer の集約結果を受け取り、Step 2 で判定を行う。

---

## 修正リトライ時

Phase 5 Step 2 で Request Changes が返された場合に使用。最大5回まで。

```
[skill-name] スキルでレビュー指摘を修正してください。

手順: まず [skill-name] スキルの SKILL.md を読んで手順に従ってください。

修正対象:
  変更ファイル: [Phase 4 の変更ファイル一覧]

タスクのコンテキスト:
  ゴール: [Phase 2 のゴール]
  完了条件: [Phase 2 の完了条件]
  制約: [Phase 2 の制約]

### レビュー指摘（以下を必ず修正すること）:
- [機能レビュー] 指摘 [N] 件: [要約]
- [AIアンチパターンレビュー] 指摘 [N] 件: [要約]
- [アーキテクチャレビュー] 指摘 [N] 件: [要約]

修正方針: レビュー指摘を優先的に解消する。指摘された箇所以外の変更は最小限にとどめること。

注意:
- [skill-name] は Phase 4 で実行したスキルと同じものを使用してください。

結果を以下の形式で返してください:
ステータス: 成功 / 失敗
修正件数: [N件]
変更ファイル: [変更したファイルの一覧]
修正内容: [各指摘に対する修正内容を簡潔に]
```

**skill-mentor の次の処理**: 修正後、再度 Step 1（多角レビュー）を実施する（最大5回）。

---

## スキルフィードバック記録時（git-skill-manager）

Phase 4 Step 3 で使用。Phase 3 で実行した各スキルについてフィードバックを記録する。
スキルごとに1回呼び出す（複数スキルがある場合は順次実行）。

```
git-skill-manager スキルでフィードバックを記録してください。

手順: まず git-skill-manager スキルの SKILL.md を読んで手順に従ってください。

フィードバック対象スキル: [skill-name]
評価: [良かった / 改善の余地あり / 問題があった]
コメント: [ユーザーフィードバックの内容 + レビュー結果のサマリー]
タスク概要: [Phase 1 のゴールを1文で要約]

注意:
- フィードバックの記録のみ行ってください。コードの変更は行わないでください。

結果を以下の形式で返してください:
ステータス: 成功 / 失敗
記録先: [記録されたファイルパスまたはGitHub issue/PR]
```

**skill-mentor の次の処理**: 全スキルのフィードバック記録完了後、`save_memory.py` で価値ある知見を保存する。

---

## フィードバック保存時

Phase 4 完了後、価値あるフィードバックがあった場合に使用。

```
以下のフィードバックを記憶に保存してください。

python ${LTM}/save_memory.py --non-interactive --no-dedup \
  --category "task-feedback" \
  --title "[タスクのゴールを要約]" \
  --summary "[ユーザーフィードバックの要約]" \
  --content "[詳細: 使用スキル、レビュー結果、ユーザーの評価]" \
  --conclusion "[次回同様のタスクで活かすべき学び]" \
  --tags feedback,[タスク種別]

結果を以下の形式で返してください:
ステータス: 成功 / 失敗
保存先: [ファイルパス]
```
