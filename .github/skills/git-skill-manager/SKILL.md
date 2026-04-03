---
name: git-skill-manager
description: スキルのインストール（pull）・チーム公開（push）・品質評価・非推奨化・アーカイブなど、Gitリポジトリを介したエージェントスキルのライフサイクル管理を担う。「スキルをpullして」「スキルをpushして」「スキルをインストールして」「スキルを有効化して」「スキルを無効化して」「スキルを非推奨にして」「スキルをアーカイブして」「スキルを昇格して」などのリクエストで発動する。
metadata:
  version: "1.2.0"
  tier: core
  category: meta
  tags:
    - skill-management
    - git
    - versioning
---

# Git Skill Manager

Gitリポジトリ経由でエージェントスキルの取得（pull）と共有（push）を行う管理システム。

## skill-creator との使い分け

**外部URLからスキルを安全に取り込みたい場合は skill-creator（モードD）を使ってください。**
skill-creator がライセンス・セキュリティ・ネットワーク通信を事前に検証し、
確認後にこのスキル（git-skill-manager）を自動で呼び出してインストールします。

このスキル（git-skill-manager）は以下の用途に特化しています:

| 操作 | 説明 |
|---|---|
| `repo add` / `pull` | 登録済みリポジトリからスキルを取得・更新 |
| `push` | ローカルスキルをチームリポジトリへ push（デフォルト: main 直接 / オプション: ブランチ作成 & PR/MR 促進） |
| `pin` / `lock` | バージョン固定・スナップショット |
| `enable` / `disable` / `profile` | スキルの有効化管理 |
| `feedback` / `promote` / `refine` | 評価・昇格・改良フロー |

## 利用者

| 呼び出し元 | 操作 | 例 |
|---|---|---|
| ユーザー直接 | repo add / pull / push / search / list / enable / disable / profile / promote / evaluate / refine / discover | 「スキルをpullして」「リポジトリを登録して」「スキルを無効化して」「スキルを昇格して」「スキルを評価して」「スキル候補を発見して」 |
| scrum-master サブエージェント | push / promote / evaluate / discover | Phase 6 のスキル共有・昇格・評価・発見時にテンプレート経由で起動される |

- ユーザー直接呼び出しの場合、対話的に確認しながら進める
- サブエージェント経由の場合、プロンプトに必要な情報（対象スキル・リポジトリ名・操作）が含まれるため、確認なしで実行する

## 動作環境

- **GitHub Copilot Chat**（Windows / macOS / Linux）および **Claude Code** で動作する
- git はインストール・認証設定済み（SSH鍵 or credential manager）
- シェルは実行環境に依存する（PowerShell、bash、zsh など）

-----

## アーキテクチャ・パス定義

詳細 → [references/architecture.md](references/architecture.md)

-----

## レジストリ

パス: `{agent_home}/skill-registry.json`（`agent_home` は `install.py` 実行時のエージェント種別で決まる。実際のパスは `skill-registry.json` の `skill_home` フィールドで確認できる）

スキルの登録情報（リポジトリ・インストール済みスキル・プロファイル・フィードバック履歴）を管理するJSONファイル。初回操作時に自動作成する。

詳細なスキーマとフィールド説明 → [references/registry-schema.md](references/registry-schema.md)

-----

## スクリプトパス

`{SCRIPTS_DIR}` = `<skill_home>/git-skill-manager/scripts`（`skill_home` は `skill-registry.json` の `skill_home` フィールド）

-----

## 操作一覧

|操作             |トリガー例               |
|---------------|--------------------|
|**repo add**   |「リポジトリを登録して」        |
|**repo list**  |「登録リポジトリ一覧」         |
|**repo remove**|「リポジトリを削除して」        |
|**pull**       |「スキルをpullして」「スキルを取得」|
|**push**       |「スキルをpushして」「スキルを共有」「ブランチを切ってpushして」「PRを作ってpushして」|
|**list**       |「インストール済みスキル一覧」     |
|**search**     |「リポジトリにあるスキルを探して」   |
|**search --refresh**|「最新のスキルを検索して」  |
|**enable**     |「スキルを有効化して」         |
|**disable**    |「スキルを無効化して」         |
|**pin**        |「スキルを固定して」「バージョンをpinして」|
|**unpin**      |「スキルの固定を解除して」       |
|**lock**       |「全スキルをロックして」        |
|**unlock**     |「全スキルのロックを解除して」     |
|**promote**    |「このスキルを他でも使えるようにして」「スキルを昇格して」|
|**profile use**|「プロファイルを切り替えて」      |
|**profile create**|「プロファイルを作成して」    |
|**profile list**|「プロファイル一覧」         |
|**profile delete**|「プロファイルを削除して」    |
|**feedback**   |「フィードバックを記録して」「良かった/改善したい/うまくいかなかった」|
|**refine**     |「スキルを改良して」「フィードバックを反映して」「改善待ちを処理して」|
|**discover**   |「スキル候補を探して」「履歴からスキルを発見して」「新しいスキルを見つけて」|
|**evaluate**   |「スキルを評価して」「試用中スキルを確認して」「ワークスペーススキルを整理して」|
|**diff**       |「スキルの差分を見せて」「リポジトリ間の違いを確認して」|
|**sync**       |「マージしたスキルを全リポジトリに配信して」「スキルを同期して」|
|**merge**      |「スキルをマージして」「リポジトリ間のスキルを統合して配信して」|
|**changelog**  |「スキルの変更履歴を生成して」「CHANGELOGを作って」|
|**bump**       |「バージョンを上げて」「パッチバージョンアップして」「メジャーバージョンアップして」|
|**auto-update**|「自動更新を有効化して」「更新チェックして」「自動更新の設定を見せて」|
|**snapshot**   |「スナップショットを保存して」「一覧を見せて」|
|**rollback**   |「元に戻して」「前の状態に戻して」「pullを取り消して」|
|**metrics**    |「メトリクスを見せて」「スキルの実行統計を確認」|
|**metrics-detail**|「○○のメトリクスを詳しく」「スキルの実行時間の推移を見たい」|
|**metrics-co** |「どのスキルが一緒に使われてる？」「共起分析して」|
|**metrics-collect**|「メトリクスを集計して」「ログを再集計して」|
|**deps**       |「依存関係を確認して」「スキルの前提が揃ってるか確認して」|
|**deps-graph** |「依存グラフを見せて」「スキルの依存関係を図示して」|
|**deprecate**  |「スキルを非推奨にして」「○○を deprecated にして」「代替スキルを○○に変えて」|
|**archive**    |「スキルをアーカイブして」「deprecated スキルを整理して」|

-----

## repo add

```bash
# 接続確認
git ls-remote $REPO_URL HEAD

# 成功したらレジストリに追加
cd {SKILL_HOME}/../..   # skill-registry.json があるディレクトリ
python {SCRIPTS_DIR}/repo.py add <name> <url>
python {SCRIPTS_DIR}/repo.py add <name> <url> --readonly
python {SCRIPTS_DIR}/repo.py add <name> <url> --priority 1 --skill-root .github/skills
python {SCRIPTS_DIR}/repo.py list
python {SCRIPTS_DIR}/repo.py remove <name>
```

→ 実装: `scripts/repo.py` — `add_repo()`, `list_repos()`, `remove_repo()`

-----

## pull

```bash
python {SCRIPTS_DIR}/pull.py                          # 全リポジトリから全スキルをpull
python {SCRIPTS_DIR}/pull.py --skill <name>           # 特定スキルのみpull
python {SCRIPTS_DIR}/pull.py --repo <repo-name>       # 特定リポジトリからpull
python {SCRIPTS_DIR}/pull.py --no-interactive         # 非対話モード（サブエージェント経由）
```

→ 実装: `scripts/pull.py` — `pull_skills()`

競合解決・ロジック詳細 → [references/examples.md](references/examples.md)

-----

## push

```bash
python {SCRIPTS_DIR}/manage.py push                                     # 全スキルを全書き込み可能リポジトリへpush
python {SCRIPTS_DIR}/manage.py push --skills skill-a,skill-b            # 特定スキルをpush
python {SCRIPTS_DIR}/manage.py push --repos team-skills                 # 特定リポジトリへpush
python {SCRIPTS_DIR}/manage.py push --skills my-skill --msg "feat: ..." # コミットメッセージ指定
```

→ 実装: `scripts/push.py` — `push_skill()`, `push_all_skills()`
→ `scripts/manage.py` — `push_to_main()`

デフォルトは main ブランチへの直接 push。ユーザーが「ブランチを切って」「PR を作って」と要求した場合のみ `add-skill/<name>` ブランチを作成して push し、PR/MR 作成を促す。

-----

## list

```bash
python {SCRIPTS_DIR}/manage.py list
```

→ 実装: `scripts/manage.py` — `list_skills()`

インストール済みスキルの一覧を表示。有効/無効、ソースリポジトリ、コミットハッシュ、pin状態、バージョンを表示する。

- `v1.2.3` — ローカルのバージョン
- `v1.2.3 ⬆️` — ローカルが中央より新しい（version_ahead）
- `v1.2.3 (central: v1.2.4)` — 中央に新しいバージョンがある（pull 推奨）

-----

## search

```bash
python {SCRIPTS_DIR}/manage.py search                             # 全スキルを一覧（インデックスキャッシュ使用）
python {SCRIPTS_DIR}/manage.py search --keyword converter         # キーワード検索
python {SCRIPTS_DIR}/manage.py search --repo team-skills          # リポジトリ絞り込み
python {SCRIPTS_DIR}/manage.py search --refresh                   # リモートから最新インデックスを取得して検索
```

デフォルトではレジストリ内の `remote_index` を検索する（ネットワーク不要、即座に結果を返す）。
`--refresh` 指定時はリモートから最新情報を取得してインデックスを更新してから検索する。
インデックスが空の場合（初回）は自動的に `--refresh` と同様の動作をする。

→ 実装: `scripts/manage.py` — `search_skills()`

-----

## enable / disable

```bash
python {SCRIPTS_DIR}/manage.py enable <skill-name>
python {SCRIPTS_DIR}/manage.py disable <skill-name>
```

スキルの有効・無効を切り替える。無効化されたスキルはディスク上に残るが、`discover_skills.py` のメタデータ収集から除外される（コンテキストウィンドウを節約）。

→ 実装: `scripts/manage.py` — `enable_skill()`, `disable_skill()`

-----

## pin / unpin

```bash
python {SCRIPTS_DIR}/manage.py pin <skill-name>                   # 現在のコミットに固定
python {SCRIPTS_DIR}/manage.py pin <skill-name> --commit abc1234  # 指定コミットに固定
python {SCRIPTS_DIR}/manage.py unpin <skill-name>
```

スキルを特定のコミットハッシュに固定する。pin されたスキルは pull 時にそのコミットの内容を取得し、新しいバージョンには更新されない。

→ 実装: `scripts/manage.py` — `pin_skill()`, `unpin_skill()`

-----

## lock / unlock

```bash
python {SCRIPTS_DIR}/manage.py lock
python {SCRIPTS_DIR}/manage.py unlock
```

全インストール済みスキルのバージョンを一括で固定・解除する。チームで同じバージョンのスキルセットを共有するときに使う。

→ 実装: `scripts/manage.py` — `lock_all()`, `unlock_all()`

-----

## promote

```bash
python {SCRIPTS_DIR}/manage.py promote .github/skills
python {SCRIPTS_DIR}/manage.py promote <workspace-skill-dir>
```

ワークスペースのスキルディレクトリ（`<workspace-skill-dir>`）のスキルをユーザー領域（`<AGENT_HOME>/skills/`）にコピーし、リポジトリにも push する。プロジェクト固有でないスキルを他のプロジェクトでも再利用可能にする。

→ 実装: `scripts/manage.py` — `promote_skills()`

-----

## ワークスペーストライアルフロー

VSCode チャット経由で作成されたスキルはワークスペースのスキルディレクトリ（`<workspace-skill-dir>`）に置かれ、試用してから昇格する。

ライフサイクル・評価フロー詳細 → [references/workspace-trial.md](references/workspace-trial.md)

-----

## feedback

```bash
python {SCRIPTS_DIR}/record_feedback.py <skill-name> ok
python {SCRIPTS_DIR}/record_feedback.py <skill-name> needs-improvement --note "改善点のメモ"
python {SCRIPTS_DIR}/record_feedback.py <skill-name> broken --note "エラー内容"
```

直前に実行したスキルの満足度をユーザーに確認し、レジストリに記録する。
スキル単体起動後に `copilot-instructions.md` の指示で自動的に呼ばれる。

→ 実装: `scripts/record_feedback.py`

フィードバック記録の詳細フロー・しきい値 → [references/feedback-loop.md](references/feedback-loop.md)

-----

## evaluate

`skill-evaluator` スキルを呼び出してワークスペース・インストール済みスキルを評価する。評価フロー詳細 → [references/feedback-loop.md](references/feedback-loop.md)

-----

## refine

```bash
python {SCRIPTS_DIR}/manage.py refine <skill-name>
# 改良完了後:
python {SCRIPTS_DIR}/manage.py mark-refined <skill-name>
```

蓄積されたフィードバックをもとに、スキルの改良フローを開始する。ワークスペーススキルとインストール済みスキル（user-space / リポジトリ管理）の両方に対応する。

→ 実装: `scripts/manage.py` — `refine_skill()`, `mark_refined()`

スクリプト出力の `REFINE_COMPLETE_CMD:` 行に示されたコマンドを**必ず実行する**（`pending_refinement` フラグの解除）。

-----

## diff / sync / merge

```bash
python {SCRIPTS_DIR}/manage.py diff <skill-name>
python {SCRIPTS_DIR}/manage.py diff <skill-name> --repos team-skills,personal

python {SCRIPTS_DIR}/manage.py sync <skill-name>
python {SCRIPTS_DIR}/manage.py sync <skill-name> --repos team-skills,personal

python {SCRIPTS_DIR}/manage.py merge <skill-name>
```

複数リポジトリに分岐した同名スキルを比較・統合・配信するクロスリポジトリ操作。

| 操作 | 用途 |
|---|---|
| `diff` | リポジトリ間の差分を表示（マージ前確認） |
| `sync` | マージ済みスキルを複数リポジトリへ一括 push |
| `merge` | diff → skill-creator → sync を一括実行 |

詳細な処理フローと出力例 → [references/cross-repo-ops.md](references/cross-repo-ops.md)

-----

## changelog / bump

```bash
python {SCRIPTS_DIR}/manage.py changelog <skill-name>
python {SCRIPTS_DIR}/manage.py changelog <skill-name> --dry-run

python {SCRIPTS_DIR}/manage.py bump <skill-name>               # patch (デフォルト)
python {SCRIPTS_DIR}/manage.py bump <skill-name> --type minor
python {SCRIPTS_DIR}/manage.py bump <skill-name> --type major
```

スキルのバージョン管理操作。

- **changelog**: コミット履歴とフロントマターのバージョン変更から `CHANGELOG.md` を自動生成する
- **bump**: SKILL.md の `metadata.version` をセマンティックバージョニングに従ってインクリメントする（`X.Y.Z` 形式）

コマンド例・バージョン指針・処理フロー・タイミング → [references/version-management.md](references/version-management.md)

-----

## discover

`skill-creator`（モードC）を起動し、直近のチャット履歴から新しいスキル候補を発見する。スクリプト呼び出しなし — エージェントが skill-creator を直接起動する。

処理フロー詳細 → [references/version-management.md](references/version-management.md)

-----

## metrics

```bash
python {SCRIPTS_DIR}/metrics_report.py
python {SCRIPTS_DIR}/metrics_report.py --skill <skill-name>   # 特定スキルの詳細
python {SCRIPTS_DIR}/metrics_report.py --co                   # 共起分析
python {SCRIPTS_DIR}/metrics_collector.py                     # ログを再集計
```

→ 実装: `scripts/metrics_report.py`, `scripts/metrics_collector.py` | 詳細 → [references/metrics.md](references/metrics.md)

-----

## profile

```bash
python {SCRIPTS_DIR}/manage.py profile list
python {SCRIPTS_DIR}/manage.py profile create <name> <skill1,skill2,...>
python {SCRIPTS_DIR}/manage.py profile use <name>
python {SCRIPTS_DIR}/manage.py profile delete <name>
```

プロファイルはスキルの有効・無効を一括で切り替えるショートカット。プロファイルをアクティブにすると、そのプロファイルに含まれるスキルのみがコンテキストにロードされる。

→ 実装: `scripts/manage.py` — `profile_create()`, `profile_use()`, `profile_list()`, `profile_delete()`

-----

## auto-update

```bash
python {SCRIPTS_DIR}/auto_update.py check         # 更新チェック
python {SCRIPTS_DIR}/auto_update.py run           # 自動更新を実行
python {SCRIPTS_DIR}/auto_update.py configure     # 設定を表示・変更
```

セッション開始時やユーザーの指示で、リポジトリの更新を自動チェックする機能。デフォルトは無効。
セッション開始時のトリガーは `.github/copilot-instructions.md` で定義されている。

→ 実装: `scripts/auto_update.py`

動作モード・設定操作・チェック操作の詳細 → [references/auto-update.md](references/auto-update.md)

-----

## snapshot / rollback

```bash
python {SCRIPTS_DIR}/snapshot.py save
python {SCRIPTS_DIR}/snapshot.py save --label "リリース前"
python {SCRIPTS_DIR}/snapshot.py list
python {SCRIPTS_DIR}/snapshot.py restore --latest
python {SCRIPTS_DIR}/snapshot.py restore <snap-id>
python {SCRIPTS_DIR}/snapshot.py clean --keep 5
```

pull 実行時に自動でスナップショットを保存し、問題が発生した場合に元の状態へ復元する。「元に戻して」「pullを取り消して」でロールバックを発動する。

詳細 → [references/snapshot-rollback.md](references/snapshot-rollback.md)

-----

## deps

```bash
python {SCRIPTS_DIR}/manage.py deps                # 全スキルの依存関係を検証
python {SCRIPTS_DIR}/manage.py deps <skill-name>   # 特定スキルの依存関係を検証
python {SCRIPTS_DIR}/manage.py deps-graph          # 全スキルの依存グラフ（Mermaid）
python {SCRIPTS_DIR}/manage.py deps-graph <skill-name>
```

スキルの `depends_on`（必須依存）・`recommends`（推奨依存）を SKILL.md フロントマターから解析し、充足状況の検証と Mermaid 依存グラフの出力を行う。

→ 実装: `scripts/deps.py` — `check_deps()`, `show_graph()`

フロントマタースキーマ・エージェントの動作・出力例 → [references/deps.md](references/deps.md)

-----

## deprecate

スキルを非推奨化し、代替スキルへの移行を促す。

ライフサイクル: `Active → Deprecated（2スプリント）→ Archived → Removed`

SKILL.md フロントマターに `tier: deprecated` / `deprecated_by: <代替>` / `deprecated_since: <ver>` を追記し、`skill-registry.json` の `deprecated_skills` に登録する。

詳細 → [references/deprecation.md](references/deprecation.md)

-----

## archive

Deprecated 期間（2スプリント）終了後にスキルを `.github/skills/_archived/` へ移動し、レジストリの `archived_skills` へ移す。

詳細 → [references/deprecation.md](references/deprecation.md)

-----

## エラーハンドリング

→ [references/errors.md](references/errors.md)

## 使用例

操作ごとのエージェント対話例 → [references/examples.md](references/examples.md)
