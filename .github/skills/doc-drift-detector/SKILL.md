---
name: doc-drift-detector
description: 既存ドキュメント（仕様書・README・ランブック・ADR・OpenAPI）とコードベースの乖離（ドリフト）を検出するスキル。「ドキュメントの乖離を検出して」「仕様書とコードのズレを確認して」「READMEが古くなってないか確認して」「ドキュメントの鮮度をチェックして」「仕様と実装の差分を調べて」などで発動する。検証可能な記述を抽出してコードと突き合わせ、ドキュメント陳腐化・実装の仕様違反・要確認に分類した根拠付きレポートを出す。新規ドキュメントの作成は technical-writer / code-to-specs を使う。
metadata:
  version: "1.0.0"
  tier: experimental
  category: documentation
  tags:
    - documentation
    - drift-detection
    - verification
    - maintenance
    - freshness
---

# doc-drift-detector

既存ドキュメントとコードベースを突き合わせ、**事実として食い違っている箇所**を証拠付きで検出する。ドキュメントは書いた瞬間から腐り始める — このスキルは `code-to-specs`（コード→仕様書の生成）の逆方向にあたる「検証」を担う。

> **設計思想**: 「全文を読み直すレビューではなく、検証可能な主張だけを機械的に潰す。文体・構成の良し悪しは見ない」

| 境界 | 使うスキル |
|------|-----------|
| 仕様書をゼロから逆生成したい | `code-to-specs` |
| README・ガイドを新規作成したい | `technical-writer` |
| ドキュメントの品質・読みやすさをレビューしたい | `agent-reviewer`（document 観点） |
| **既存ドキュメントが今のコードと一致しているか検証したい** | **本スキル** |

## 設計原則

- **事実ドリフトのみ** — パス・コマンド・API・設定値・手順など検証可能な記述だけを対象にする。文体・誤字・構成は指摘しない
- **証拠ペア必須** — すべての指摘に `[DOC: ファイル:行]` と `[CODE: ファイル:行]`（または「該当なし」）の両方を付ける。証拠を示せない指摘は出さない
- **読み取り専用がデフォルト** — まずレポートを出し、修正はユーザー承認後に行う
- **優先度駆動** — 全ドキュメントを均等に舐めず、git 履歴から「腐っている可能性が高い順」に検証する

## ワークフロー

### Step 1: 対象ドキュメントの棚卸し

1. ドキュメントを探索する: `README*`・`docs/**`・`CONTRIBUTING*`・ランブック・ADR・OpenAPI/AsyncAPI・`*.md` のコードコメント外文書
2. 種別（仕様書 / README / ランブック / ADR / API仕様）と検証優先度を付けた一覧をユーザーに提示し、スコープを確定する
3. 対象が多い場合は「最終更新が古い順 + 参照頻度が高そうな順」で上位を提案する

### Step 2: 検証可能な主張（claim）の抽出

各ドキュメントから以下の種別の主張を抽出し、`.drift-work/claims.json` に記録する:

| 種別 | 例 | 検証方法 |
|------|----|---------|
| `path` | 「設定は `config/app.yaml` にある」 | ファイル・ディレクトリの実在確認 |
| `command` | 「`npm run build` でビルド」 | マニフェスト（package.json 等）の scripts と照合 |
| `api` | 「`POST /v1/orders` が注文を作成」 | ルーター実装・OpenAPI 定義と照合 |
| `config` | 「タイムアウトは 30 秒」「環境変数 `API_KEY`」 | 設定ファイル・環境変数読み取りコードと照合 |
| `dependency` | 「React 17 を使用」 | lockfile・マニフェストの実バージョンと照合 |
| `structure` | 「認証は middleware 層で行う」 | 該当モジュールの実在と責務の確認 |
| `procedure` | 手順書のステップ列 | 各ステップ内のパス・コマンドを個別検証 |

### Step 3: コードとの突き合わせ

- claim ごとに Step 2 の検証方法で実体を確認し、`verified` / `drifted` / `unverifiable` を記録する
- 広域の確認（structure 系）は `Explore` エージェント等の探索を活用する
- 動的にしか検証できない主張（実行時挙動）は推測で断定せず `unverifiable` とする

### Step 4: git 履歴による疑い箇所の優先度付け

1. 各ドキュメントの最終更新コミットを特定する: `git log -1 --format=%H -- <doc>`
2. それ以降に、ドキュメントが言及するコード領域へ入った変更量を集計する: `git log --stat <doc最終更新>..HEAD -- <言及パス>`
3. 「ドキュメント更新を伴わない大きな実装変更」があった領域の claim を優先的に深掘りする

### Step 5: 分類と判定

drifted となった claim を以下に分類する:

| 分類 | 意味 | 是正の向き |
|------|------|-----------|
| `DOC-STALE` | コードが正、ドキュメントが古い | ドキュメントを更新 |
| `CODE-DRIFT` | ドキュメント（仕様）が正、実装が逸脱 | コードを修正（要ユーザー判断） |
| `UNKNOWN` | どちらが正か判断できない | `[ASK SME]` として要確認リストへ |

正誤の判断に迷う場合は安易に `DOC-STALE` へ倒さず `UNKNOWN` とする。severity は `critical`（手順が壊れる・誤動作を招く）/ `major`（誤解を招く）/ `minor`（軽微な不整合）。

### Step 6: レポート出力と是正提案

`.drift-work/drift-report.md` に出力する:

```markdown
# ドキュメントドリフトレポート

## サマリ
| ドキュメント | claim数 | verified | drifted | unverifiable | 鮮度スコア |
|---|---|---|---|---|---|

## 指摘一覧
### D-001 [DOC-STALE / critical] ビルドコマンドが存在しない
- 主張: 「`npm run build:prod` でビルドする」 [DOC: README.md:42]
- 実体: scripts に `build:prod` は存在せず `build` のみ [CODE: package.json:12]
- 是正案: README.md:42 を `npm run build` に修正

## 要確認リスト（UNKNOWN）
## 検証不能リスト（unverifiable）
```

是正の実行（ユーザー承認後）:
- `DOC-STALE` → 修正パッチを適用する。大規模な書き直しは `technical-writer`、章の再生成は `code-to-specs` へ委譲する
- `CODE-DRIFT` → 修正方針をユーザーに確認してから着手する（必要なら `systematic-debugging` へ）
- `UNKNOWN` → 要確認リストとして提示し、勝手に解決しない

## 連携スキル

- `code-to-specs` — 陳腐化した仕様書の章を再生成する
- `technical-writer` / `runbook-author` — README・手順書の書き直し
- `agent-reviewer` — 事実性以外（構成・読みやすさ）のレビュー
- `commit-pr-writer` — 是正コミット・PR の説明文生成

## ガードレール

| 制限 | 内容 |
|------|------|
| 事実性 | 証拠ペアのない指摘を出さない。検証できない主張を「ドリフト」と断定しない |
| スコープ | 文体・誤字・構成の指摘はしない（事実ドリフトのみ） |
| 変更権限 | レポートまでは読み取り専用。修正はユーザー承認後、`DOC-STALE` のみ自動適用可 |
| 機密 | レポートに秘匿値（鍵・トークン）を転記しない |

## 再開プロトコル

「ドリフト検出を再開して」と言われたら `.drift-work/claims.json` の検証済み claim をスキップし、未検証分から続行する。
