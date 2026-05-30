# コミット・PR 規約リファレンス

## Conventional Commits

```
<type>(<scope>)<!>: <subject>

<body>

<footer>
```

### type 一覧

| type | 用途 | semver |
|------|------|--------|
| `feat` | 新機能 | MINOR |
| `fix` | バグ修正 | PATCH |
| `perf` | パフォーマンス改善（挙動は不変） | PATCH |
| `refactor` | 挙動を変えない内部整理 | - |
| `docs` | ドキュメントのみ | - |
| `style` | フォーマット・空白等（ロジック不変） | - |
| `test` | テストの追加・修正 | - |
| `build` | ビルドシステム・依存関係 | - |
| `ci` | CI 設定・スクリプト | - |
| `chore` | その他雑務（リリース作業等） | - |
| `revert` | コミットの取り消し | 状況による |

### scope

変更が及ぶ範囲（モジュール名・パッケージ名・機能領域）。任意。例: `feat(auth):` `fix(api):`。

### 破壊的変更

次のいずれかで示す。両方併記も可。

- type の直後に `!`: `feat!: ...` / `feat(api)!: ...`
- フッターに `BREAKING CHANGE: <説明>`

### subject の書き方

- 命令形・現在形（"add" であって "added"/"adds" ではない）
- 先頭は小文字、末尾にピリオドを付けない
- 50文字程度を目安に簡潔に
- 日本語リポジトリの場合は既存履歴の言語（日本語/英語）に合わせる

### 良い例 / 悪い例

```
✅ fix(auth): reject expired JWT before authorization check
❌ fix: bug
❌ updated some files
❌ WIP
```

---

## type → CHANGELOG セクション マッピング

| Conventional type | Keep a Changelog セクション |
|-------------------|----------------------------|
| `feat` | Added |
| `fix` | Fixed |
| `refactor` / `perf` / 挙動変更 | Changed |
| 機能・API の削除 | Removed |
| 脆弱性修正（`fix` のうちセキュリティ） | Security |
| `BREAKING CHANGE` | Breaking（先頭に配置し ⚠️ で強調） |
| `docs` / `style` / `test` / `ci` / `chore` | 原則 CHANGELOG に載せない |

### CHANGELOG テンプレート

```markdown
# Changelog

すべての注目すべき変更をこのファイルに記録する。
形式は [Keep a Changelog](https://keepachangelog.com/) に準拠し、
バージョニングは [Semantic Versioning](https://semver.org/) に従う。

## [Unreleased]

## [1.2.0] - 2026-05-30
### Added
- ...

### Fixed
- ...

### Breaking
- ⚠️ ...
```

---

## semver 判定の優先順位

複数の変更が混在する場合、**最も影響の大きい bump を採用する**。

```
BREAKING CHANGE あり → MAJOR
（無く）feat あり     → MINOR
（無く）fix/perf のみ → PATCH
```

- メジャーバージョンが `0.x.y`（初期開発フェーズ）の場合、破壊的変更を MINOR、機能追加を PATCH に倒す運用も一般的。どちらを採るかをユーザーに提示して決める。
- プレリリースは `1.0.0-rc.1` のように `-` で suffix を付ける。

---

## PR 本文の節構成（テンプレート未定義時の既定）

| 節 | 内容 |
|----|------|
| 概要 | 何を解決するか・背景（why） |
| 変更点 | 挙動・意図ベースの箇条書き（ファイル列挙にしない） |
| テスト | 検証方法・追加テスト・チェックリスト |
| 影響範囲・注意点 | 破壊的変更・マイグレーション・ロールバック |
| 関連 | Issue / チケットへのリンク |

`.github/PULL_REQUEST_TEMPLATE.md` が存在する場合は、上記より**テンプレートの節構成を優先**する。
