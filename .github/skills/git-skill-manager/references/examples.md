# 使用例

## 目次

- [初回インストール](#初回インストール)
- [初回セットアップ](#初回セットアップ)
- [readonlyリポジトリの登録](#readonlyリポジトリの登録)
- [pull（キャッシュ活用）](#pullキャッシュ活用)
- [push](#push)
- [スキルの無効化](#スキルの無効化)
- [検索（オフライン）](#検索オフライン)
- [検索（最新を取得）](#検索最新を取得)
- [スキルのバージョン固定](#スキルのバージョン固定)
- [全スキルのロック](#全スキルのロック)
- [スキルの昇格（promote）](#スキルの昇格promote)
- [プロファイル切り替え](#プロファイル切り替え)

## 初回インストール

```
git clone https://github.com/myorg/agent-skills.git
python agent-skills/install.py
```

コアスキル（scrum-master, git-skill-manager, skill-creator, sprint-reviewer, codebase-to-skill）がユーザー領域にコピーされ、ソースリポジトリがレジストリに自動登録される。2回目以降の実行はスキルの上書き更新になる（レジストリの既存設定は保持）。

## 初回セットアップ

```
ユーザー: 「https://github.com/myorg/skills.git をスキルリポジトリに登録して」

Copilot:
  1. git ls-remote で接続確認
  2. レジストリ作成、リポジトリ追加（readonlyにするか確認、priorityを確認）
  3. 「登録しました。pullしますか？」
```

## readonlyリポジトリの登録

```
ユーザー: 「https://github.com/otherteam/skills.git を参照専用で登録して」

Copilot:
  1. git ls-remote で接続確認
  2. readonly: true でレジストリに追加
  3. 「readonlyで登録しました。pullのみ可能です」
```

## pull（キャッシュ活用）

```
ユーザー: 「スキルを全部同期して」

Copilot:
  1. 全リポジトリを cache からfetch（初回のみclone）
  2. 各リポジトリのスキルを走査
  3. 同名競合があればユーザーに確認
  4. %USERPROFILE%\.copilot\skills\ にコピー、レジストリ更新
  5. 結果レポート（有効/無効状態も表示）
```

## push

```
ユーザー: 「今作ったスキルを team-skills にpushして」

Copilot:
  1. レジストリから team-skills の情報を取得
  2. SKILL.md の存在確認
  3. clone → ブランチ作成 → コピー → commit → push
  4. コミットハッシュとブランチ名を報告
```

## スキルの無効化

```
ユーザー: 「legacy-tool スキルを無効化して」

Copilot:
  1. レジストリの enabled を false に変更
  2. 「legacy-tool を無効化しました。再有効化は 'スキルを有効化して' で可能です」
```

## 検索（オフライン）

```
ユーザー: 「converter で検索して」

Copilot:
  1. レジストリの remote_index から keyword=converter で検索（ネットワーク不要）
  2. 結果を表示（インデックス更新日も表示）
```

## 検索（最新を取得）

```
ユーザー: 「最新のスキルを検索して」

Copilot:
  1. 全リポジトリから fetch してインデックスを更新
  2. 更新後のインデックスから検索結果を表示
```

## スキルのバージョン固定

```
ユーザー: 「docx-converter を今のバージョンに固定して」

Copilot:
  1. 現在の commit_hash を pinned_commit に設定
  2. 「docx-converter を a1b2c3d に固定しました」
```

## 全スキルのロック

```
ユーザー: 「全スキルをロックして」

Copilot:
  1. 全 installed_skills の commit_hash を pinned_commit に設定
  2. ロックされたスキル一覧を表示
```

## スキルの昇格（promote）

```
ユーザー: 「ワークスペースのスキルを他のプロジェクトでも使えるようにして」

Copilot:
  1. ワークスペースのスキルディレクトリ（例: `$workspace/<workspace-skill-dir>/`）をスキャン、候補をリストアップ
  2. ユーザーが昇格するスキルを選択
  3. ~/.copilot/skills/ にコピー、レジストリに登録
  4. push 先リポジトリをユーザーが選択
  5. 選択リポジトリに push（ブランチ作成）
```

## プロファイル切り替え

```
ユーザー: 「フロントエンド開発用のプロファイルに切り替えて」

Copilot:
  1. frontend プロファイルをアクティブに設定
  2. 「frontend プロファイルをアクティブにしました: react-guide, css-linter, storybook」
```
