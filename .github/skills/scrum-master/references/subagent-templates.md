# サブエージェントへの指示テンプレート

## 目次

- [重要な注意事項](#重要な注意事項)
- [requirements-definer 呼び出し時](#requirements-definer-呼び出し時)
- [skill: null タスク実行時](#skill-null-タスク実行時)
- [スキル実行時](#スキル実行時)
- [スキル作成時](#スキル作成時)
- [スキル改良時](#スキル改良時)
- [コードベースからスキル生成時](#コードベースからスキル生成時)
- [スキル招募時](#スキル招募時)
- [スプリントレビュー時](#スプリントレビュー時)
- [worktree 並列実行時](#worktree-並列実行時)
- [スキルフィードバック収集時](#スキルフィードバック収集時)
- [スキル昇格時](#スキル昇格時)
- [スキル評価時](#スキル評価時)
- [スキル共有時](#スキル共有時)
- [スキル発見時](#スキル発見時)

## 重要な注意事項

- SKILL.md の内容をプロンプトに埋め込まない。ファイルパスだけ渡し、サブエージェント自身に読ませる。これによりプロンプトを短く保ち、安定性を確保する。
- すべてのテンプレートに戻り値の形式指定を含める。これによりscrum-masterが結果を確実にパースできる。

## requirements-definer 呼び出し時

```
requirements-definer スキルでユーザーと対話して要件を定義する。

手順: まず ${SKILLS_DIR}/requirements-definer/SKILL.md を読んで手順に従ってください。
ユーザーのプロンプト: [元のプロンプト]
出力先: requirements.json（作業ディレクトリのルート）
出力スキーマ: ${SKILLS_DIR}/requirements-definer/references/requirements-schema.md を参照すること。

完了後、requirements.json を出力して終了してください。計画立案やタスク分解は行わないこと。

結果を以下の形式で返してください:
ステータス: 成功 / 失敗
goal: [requirements.json の goal フィールドの値]
要件数: 機能要件 [N] 件 / 非機能要件 [M] 件
サマリー: [1〜2文で結果を説明]
```

**scrum-master の次の処理**: サブエージェント完了後、`requirements.json` を読み込んでバックログを作成する（Phase 2）。

## skill: null タスク実行時

スキル不要な調査・判断・軽微な編集のテンプレート。

```
以下のタスクを実行してください。

タスク: [action]
コンテキスト: [先行タスクのresultを1〜2行で要約]
完了基準: [done_criteria]

結果を以下の形式で返してください:
ステータス: 成功 / 失敗
サマリー: [1〜2文で結果を説明]
```

## スキル実行時

```
[skill-name] スキルを実行する。

手順: まず [skill-md-path] を読んで手順に従ってください。
タスク: [action]
コンテキスト: [先行タスクのresultを1〜2行で要約]
完了基準: [done_criteria]
フィードバック: 実行後フィードバック節はスキップしてください。フィードバックはスプリント終了時に一括で収集されます。

結果を以下の形式で返してください:
ステータス: 成功 / 失敗
サマリー: [1〜2文で結果を説明]
```

## スキル作成時

```
skill-creator スキルで新しいスキルを作成する。

手順: まず ${SKILLS_DIR}/skill-creator/SKILL.md を読んで手順に従ってください。
作成するスキル: [概要]
配置先: ${SKILLS_DIR}/
注意: ユーザーとの対話は行わず、提供された概要と以下の仕様から判断して進めること。
仕様: [scrum-masterが事前にユーザーから収集した要件・方針]

結果を以下の形式で返してください:
ステータス: 成功 / 失敗
作成されたスキル名: [name]
サマリー: [1〜2文で結果を説明]
```

## スキル改良時

```
skill-creator スキルで既存スキルを改良する。

手順: まず ${SKILLS_DIR}/skill-creator/SKILL.md を読んで手順に従ってください。
対象スキル: [skill-md-path]
改良内容: [改善点・追加機能・分割方針の説明]
配置先: ${SKILLS_DIR}/
分割する場合は、元スキルの機能が漏れなく引き継がれることを確認すること。
注意: ユーザーとの対話は行わず、提供された改良内容をもとに判断して進めること。
push指示: [インストール済みスキルかつ source_repo がリポジトリ名の場合は「改良後に git-skill-manager push を実行すること」と記載。それ以外は「なし」]

結果を以下の形式で返してください:
ステータス: 成功 / 失敗
変更されたスキル名: [name(s)]
push実行: [実行した / 不要]
サマリー: [1〜2文で結果を説明]
```

**scrum-master の事前確認**: インストール済みスキル（source_repo がリポジトリ名）の場合は `push指示` に「改良後に git-skill-manager push を実行すること。リポジトリ: [repo-name]」を記載する。`local` または `workspace` の場合は「なし」。

## コードベースからスキル生成時

```
codebase-to-skill スキルで既存コードベースからスキルを生成する。

手順: まず ${SKILLS_DIR}/codebase-to-skill/SKILL.md を読んで手順に従ってください。
対象コードベース: [codebase-path]
生成するスキルの用途: [タスクのactionと不足しているスキルの概要]
配置先: ${SKILLS_DIR}/
既存スキルの改良の場合: [既存スキルのパスと不足点。新規作成の場合は「なし」]
スコープ: [scrum-masterが事前にユーザーから確認したスコープとフォーカス]
注意: ユーザーとの対話は行わず、提供されたスコープをもとに判断して進めること。

結果を以下の形式で返してください:
ステータス: 成功 / 失敗
作成されたスキル名: [name]
サマリー: [1〜2文で結果を説明]
```

## スキル招募時

```
skill-recruiter スキルで外部リポジトリからスキルを取得・検証する。

手順: まず ${SKILLS_DIR}/skill-recruiter/SKILL.md を読んで手順に従ってください。
取得するスキルのURL: [url]
用途: [タスクのactionと不足しているスキルの概要]

結果を以下の形式で返してください:
ステータス: 成功 / 失敗
取得されたスキル名: [name]
サマリー: [1〜2文で結果を説明]
```

## スプリントレビュー時

```
sprint-reviewer スキルでスプリントのレビューとレトロスペクティブを実施する。

手順: まず ${SKILLS_DIR}/sprint-reviewer/SKILL.md を読んで手順に従ってください。
ゴール: [goal]
スプリント番号: [N]
スプリント計画:
- [計画時に選択したタスクと優先度の要約]
実行ログサマリー:
- [予定どおり進んだ点 / 遅延・失敗が出た点]
タスク一覧:
- [task-id]: action=[action], done_criteria=[done_criteria], status=[status], result=[result]
- ...

結果を以下の形式で返してください:
レビュー: [ゴール進捗の評価 1〜2文]
進め方レビュー: [スプリントプランと実行プロセスの評価 1〜2文]
レトロスペクティブ: [改善点 1〜2文]
次スプリント反映アクション:
- [改善アクション1]
- [改善アクション2]
ブロッカー: [あれば列挙、なければ「なし」]
```

## worktree 並列実行時

同一ファイルの独立したセクションを複数タスクが並列変更する場合に使用する。
scrum-master 自身が以下の手順を実行する（サブエージェントへの指示ではなく、scrum-master が直接実施する）。

**前提確認**: 各タスクの action と done_criteria から変更箇所が重複しないことを事前に確認すること。不確かな場合は戦略A（ウェーブ分割）を選択する。

**手順:**

1. **worktree を作成する**（タスクごとに1つ）:
   ```bash
   git worktree add /tmp/wt-[task-id] -b wt/[task-id] HEAD
   ```

2. **各タスクのサブエージェントを並列起動する**。各サブエージェントへの指示にworktreeパスを明示する:
   ```
   [skill-name] スキルを実行する。

   手順: まず [skill-md-path] を読んで手順に従ってください。
   作業ディレクトリ: /tmp/wt-[task-id]（このディレクトリ内で作業すること）
   タスク: [action]
   コンテキスト: [先行タスクのresultを1〜2行で要約]
   完了基準: [done_criteria]
   完了後: 変更ファイルを git add && git commit -m "[task-id]: [action]" で /tmp/wt-[task-id] にコミットすること
   フィードバック: 実行後フィードバック節はスキップしてください。

   結果を以下の形式で返してください:
   ステータス: 成功 / 失敗
   コミットSHA: [git rev-parse HEAD の出力]
   サマリー: [1〜2文で結果を説明]
   ```

3. **全サブエージェント完了後、変更をメインブランチにマージする**:
   ```bash
   # 各worktreeブランチをメインブランチにマージ（fast-forwardしない）
   git merge --no-ff wt/[task-id-1] wt/[task-id-2] -m "merge: [task-id-1], [task-id-2] の並列実行結果をマージ"
   ```
   - **コンフリクトが発生した場合**: マージを中断（`git merge --abort`）してユーザーに報告し、戦略Aに切り替えてウェーブ分割で再実行する

4. **worktree を削除する**:
   ```bash
   git worktree remove /tmp/wt-[task-id-1]
   git worktree remove /tmp/wt-[task-id-2]
   git branch -d wt/[task-id-1] wt/[task-id-2]
   ```

**注意事項**:
- worktree のパスは `/tmp/wt-[task-id]` の形式で一意にする
- マージ後にコンフリクトが解決できない場合は、worktree を削除してからウェーブ分割に切り替える
- この手順は scrum-master が直接実行するものであり、サブエージェントに委譲しない

## スキルフィードバック収集時

```
以下のスキルについて、提供されたフィードバックを record_feedback.py で記録してください。

対象スキル: [skill-name1, skill-name2, ...]
フィードバック:
- [skill-name1]: [verdict: ok / needs-improvement / broken] — [note]
- [skill-name2]: ...

注意: ユーザーへの確認は不要。scrum-masterが既に収集済みのフィードバックを記録するだけでよい。

各スキルについて record_feedback.py を実行する（git-skill-manager がない環境ではスキップ）:
python -c "import os,sys,subprocess; s=os.path.join(os.path.expanduser('~'),'.copilot','skills','git-skill-manager','scripts','record_feedback.py'); subprocess.run([sys.executable,s,'<skill-name>','--verdict','<verdict>','--note','<note>']) if os.path.isfile(s) else None"

結果を以下の形式で返してください:
ステータス: 成功 / スキップ（git-skill-manager なし）
記録済みフィードバック数: [N 件]
サマリー: [1〜2文で結果を説明]
```

**scrum-master の事前作業**: このサブエージェントを起動する前に、scrum-master 自身がユーザーに以下の形式で一括確認する:
```
このスプリントで使用したスキルのフィードバックを収集します。

- [skill-name1]: 1. 問題なかった (ok) / 2. 改善点がある (needs-improvement) / 3. うまくいかなかった (broken)
- [skill-name2]: ...
```
回答を収集後、フィードバック内容をテンプレートの「フィードバック:」欄に埋め込んでサブエージェントへ渡す。

**scrum-master の次の処理**: フィードバック収集完了後、Phase 6 の次のステップ（ワークスペーススキルの棚卸し）へ進む。

## スキル昇格時

```
git-skill-manager スキルでワークスペーススキルをユーザー領域に昇格する。

手順: まず ${SKILLS_DIR}/git-skill-manager/SKILL.md を読んで promote 操作の手順に従ってください。
対象スキル: [skill-name]
操作: promote
補足: ${SKILLS_DIR}/[skill-name]/ を ~/.copilot/skills/ にコピーし、
      書き込み可能なリポジトリがあれば push も提案してください。

結果を以下の形式で返してください:
ステータス: 成功 / 失敗
昇格先: [~/.copilot/skills/<name>]
push先: [repo-name または「なし」]
サマリー: [1〜2文で結果を説明]
```

## スキル評価時

```
skill-evaluator スキルで全スキルを評価する（レポートのみモード）。

手順: まず ${SKILLS_DIR}/skill-evaluator/SKILL.md を読んで手順に従ってください。
操作: 全スキル（ワークスペース + インストール済み）を評価する。
     python .github/skills/skill-evaluator/scripts/evaluate.py --type all を実行すること。
モード: レポートのみ。ユーザーへの確認・promote/refine の実行は行わないこと。
       評価結果と推奨アクションを以下の形式で返すだけでよい。

結果を以下の形式で返してください:
ステータス: 成功 / 失敗
評価結果（ワークスペース）:
- [skill-name]: [推奨アクション（昇格推奨 / 要改良後昇格 / 試用継続）] — [理由1文]
評価結果（インストール済み）:
- [skill-name]: [推奨アクション（要改良 / 正常）] — [理由1文] — source_repo: [repo-name または local]
サマリー: [1〜2文で全体を説明]
```

**scrum-master の次の処理**: 評価結果を受け取り、スクラムマスター自身がユーザーに確認する。
- ワークスペーススキルの昇格推奨 → 「[skill-name] を昇格しますか？ 1. 昇格する / 2. 後で / 3. スキップ」
- 要改良スキル（ワークスペース・インストール済み共通） → 「[skill-name] を改良しますか？ 1. 今すぐ改良する / 2. 後で / 3. スキップ」
  - インストール済みかつ source_repo がリポジトリ名の場合は改良後に push も提案する（「スキル共有時」テンプレートを使用）
承認されたアクションのみ「スキル昇格時」「スキル改良時」テンプレートでサブエージェントへ委譲する。

## スキル共有時

```
git-skill-manager スキルでスキルをリポジトリに共有する。

手順: まず ${SKILLS_DIR}/git-skill-manager/SKILL.md を読んで手順に従ってください。
対象スキル: [skill-path]
操作: push
リポジトリ: [repo-name]

結果を以下の形式で返してください:
ステータス: 成功 / 失敗
サマリー: [1〜2文で結果を説明]
```

## スキル発見時

```
git-skill-manager スキルの discover 操作で、チャット履歴から新しいスキル候補を発見する。

手順: まず ${SKILLS_DIR}/git-skill-manager/SKILL.md を読んで discover 操作の手順に従ってください。
操作: discover
補足: ユーザーのチャット履歴を分析し、繰り返しワークフローをスキル候補として提案してください。
      候補が見つかれば、ユーザーに選択させてから skill-creator でスキルを生成してください。

結果を以下の形式で返してください:
ステータス: 成功 / 失敗
発見されたスキル候補数: [N 件]
生成されたスキル: [name1, name2, ...（なければ「なし」）]
サマリー: [1〜2文で結果を説明]
```
