---
name: commit-pr-writer
description: 変更内容（diff・コミット履歴）から Conventional Commits 準拠のコミットメッセージ、Pull Request のタイトル・説明文、CHANGELOG / リリースノート、semver バージョン判定を生成するスキル。「コミットメッセージを書いて」「コミットメッセージ考えて」「PRの説明を書いて」「プルリクの説明文作って」「PRタイトル考えて」「CHANGELOGを作って」「リリースノートを書いて」「バージョンを上げて」「semverどれ？」などのリクエストで発動する。
metadata:
  version: 1.0.0
  tier: experimental
  category: git
  tags:
    - git
    - commit
    - pull-request
    - changelog
    - conventional-commits
    - semver
---

# commit-pr-writer

変更内容を解析し、規約に沿った **コミットメッセージ / PR 説明文 / CHANGELOG・リリースノート / semver 判定** を生成する。

## 原則

- **diff を根拠にする** — 推測でメッセージを書かない。実際の変更（diff・コミット履歴）に基づく
- **規約に従う** — リポジトリに既存の規約（直近のコミット履歴・CONTRIBUTING・PR テンプレート）があればそれを最優先で踏襲する。無ければ Conventional Commits をデフォルトとする
- **why を一行入れる** — what（何を変えたか）は diff から読めるので、本文には why（なぜ変えたか）を残す
- **コミット・プッシュは勝手にしない** — メッセージ生成が役割。`git commit` / `git push` / PR 作成は、ユーザーが明示的に依頼した場合のみ実行する

## モード判定

依頼内容から以下のいずれかを選ぶ（複数該当時はユーザーに確認）。

| モード | 発動する依頼 | 入力 |
|--------|-------------|------|
| `commit` | コミットメッセージ生成 | `git diff --staged`（無ければ `git diff`） |
| `pr` | PR タイトル・説明文生成 | ベースブランチとの差分・コミット履歴 |
| `changelog` | CHANGELOG / リリースノート生成 | 直近タグ以降のコミット履歴 |
| `version` | semver バージョン判定 | 直近タグ以降のコミット履歴 |

---

## commit モード

### Step 1: 対象の差分を取得する

1. `git diff --staged --stat` でステージ済みの変更を確認する
2. ステージ済みが空なら `git diff --stat` を見て、対象をユーザーに確認する（「全部ステージしてからでよいか」等）
3. 規約把握のため `git log --oneline -10` で直近コミットのスタイル（prefix の有無・言語・粒度）を確認する

### Step 2: 論理単位に分割すべきか判断する

- 1 つの関心事（1 つの fix / feat / refactor）に収まる → 単一コミット
- 無関係な複数の変更が混在している → コミット分割を提案し、`git add -p` 相当の分割案（ファイル/ハンク単位）を提示する。分割実行はユーザー承認後

### Step 3: メッセージを生成する

Conventional Commits 形式（規約詳細は [references/conventions.md](references/conventions.md)）:

```
<type>(<scope>): <subject>

<body: なぜこの変更が必要だったか>

<footer: BREAKING CHANGE / 課題番号など>
```

- `type`: feat / fix / docs / style / refactor / perf / test / build / ci / chore / revert
- `subject`: 命令形・50文字程度・末尾ピリオドなし
- `body`: 72文字で折り返し。what より why を書く（任意だが推奨）
- 破壊的変更があれば `BREAKING CHANGE:` フッターまたは `type!` を必ず付ける

### Step 4: 提示する

生成したメッセージをコードブロックで提示する。ユーザーが「コミットして」と言った場合のみ:

```bash
git commit -m "<subject>" -m "<body>"
```

を実行する。リポジトリ固有のフッター規約（署名・課題リンク等）があれば踏襲する。

---

## pr モード

### Step 1: 差分とコミットを集める

1. ベースブランチを特定する（指定が無ければ `git remote show origin` の HEAD、または `main`/`master`/`develop` を推定）
2. `git log --oneline <base>..HEAD` でコミット一覧、`git diff <base>...HEAD --stat` で変更範囲を確認する
3. リポジトリに PR テンプレート（`.github/PULL_REQUEST_TEMPLATE.md`）があれば、その節構成に合わせる

### Step 2: タイトルと本文を生成する

- **タイトル**: Conventional Commits 形式 1 行（複数 type が混在するなら主目的の type を採用）
- **本文**: テンプレートが無ければ下記の既定構成を使う

```markdown
## 概要
<この PR が何を解決するか / 背景・why を 2〜4 行>

## 変更点
- <主要な変更を箇条書き（ファイル名ではなく挙動・意図ベース）>

## テスト
- [ ] <どう検証したか / 追加したテスト>

## 影響範囲・注意点
- <破壊的変更・マイグレーション・ロールバック手順など。無ければ「なし」>

## 関連
- <Issue / チケット番号>
```

### Step 3: 提示する

本文を提示する。ユーザーが「PR を作って」と言った場合のみ作成に進む（`gh pr create` または GitHub MCP ツール。利用可能な手段を確認してから実行）。

---

## changelog モード

### Step 1: 範囲を決める

1. `git describe --tags --abbrev=0` で直近タグを取得（無ければ全履歴）
2. `git log <last-tag>..HEAD --pretty=format:"%s (%h)"` でコミット件名を集める

### Step 2: 分類して整形する

[Keep a Changelog](https://keepachangelog.com/) 形式。Conventional Commits の type からセクションへマッピング（詳細は [references/conventions.md](references/conventions.md)）:

```markdown
## [x.y.z] - YYYY-MM-DD

### Added        ← feat
### Changed      ← refactor / perf / 既存挙動の変更
### Fixed        ← fix
### Removed      ← 機能削除
### Security     ← 脆弱性修正
### Breaking     ← BREAKING CHANGE
```

- ユーザー向けに意味のある変更のみ載せる（`chore`/`ci`/`style` や内部リファクタは原則除外、または末尾にまとめる）
- 各行は利用者目線の表現に言い換える（コミット件名のコピペにしない）

---

## version モード

直近タグ以降のコミットから次バージョンを判定する（semver: MAJOR.MINOR.PATCH）。

| 含まれる変更 | bump |
|--------------|------|
| `BREAKING CHANGE` / `type!` あり | **MAJOR**（0.x系は MINOR に倒す選択肢も提示） |
| `feat` あり（破壊的変更なし） | **MINOR** |
| `fix` / `perf` のみ | **PATCH** |
| `docs`/`chore`/`ci` のみ | bump 不要（必要なら PATCH を提案） |

判定結果は「現行 vX.Y.Z → 推奨 vX'.Y'.Z'」と根拠（どのコミットが MAJOR/MINOR を引き起こすか）をセットで提示する。

---

## ガードレール

| 制限 | 内容 |
|------|------|
| 自動コミット/プッシュ | 禁止。ユーザーの明示依頼時のみ実行 |
| 規約の優先順位 | リポジトリ既存規約 > Conventional Commits（既定） |
| 事実性 | diff・コミット履歴に無い内容を書かない。不明点は確認する |
| 機密 | diff 内のシークレットらしき値を本文・メッセージに転記しない |
