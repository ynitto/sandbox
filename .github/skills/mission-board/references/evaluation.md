# Mission Board スキル — 評価レポート

_評価日: 2026-03-14_

---

## 総合評価

| カテゴリ | スコア | 所見 |
| -------- | ------ | ---- |
| 構造・明確さ | ✅ 良好 | サブコマンドが明確、手順が網羅的 |
| 自律性設計 | ✅ 良好 | 最小往復・最大自己解決の原則が一貫している |
| エラーハンドリング | ✅ 良好 | stash/pop、offline端末の再割り当てが定義済み |
| Windows 互換性 | ⚠️ 要注意 | 変数定義が sh 構文のみ（PowerShell 版を追加済み） |
| GitHub Copilot 互換性 | ❌ 非対応 | 自律実行機構がないため動作不可（明記済み） |
| 外部スキル依存 | ⚠️ 要注意 | `deep-research` スキルが必須だが未インストール環境への言及なし |

---

## 詳細評価

### 1. 構造と明確さ

**良い点:**

- `SKILL.md` がメタデータ・概要を、`references/subcommands.md` が詳細手順を担う分離設計が明確
- サブコマンドが6種に整理されており、各手順にステップ番号がある
- メッセージ規約（ファイル名・YAML フロントマター）が具体的で曖昧さがない
- worktree を SSOT として使う設計が一貫している

**改善余地:**

- `deep-research` スキルが未インストールの場合のフォールバック手順がない
- `check` サブコマンドは Preflight & Pull を「実行」と記載しているが、pull が不要なケース（読み取り専用の状況確認）も考慮できる

### 2. エラーハンドリング

**良い点:**

- `Preflight & Pull` で未コミット変更を stash して pull 後に pop する手順が明確
- コンフリクト時の対処が記載されている
- Heartbeat でオフライン端末を検出し、タスクを `@any` に再割り当てする仕組みがある

**改善余地:**

- `git push` 失敗時（ネットワークエラー、認証エラー）のリトライ手順が未記載
- orphan ブランチ作成時に既存ブランチとの衝突ケースが未記載

### 3. 自律性設計

**良い点:**

- 「最小往復・最大自己解決」の原則が具体的なチェックリストで定義されている
- DeepResearch への自動ルーティング条件（即時DR条件・蓄積DR条件）が定量的に定義されている
- `pull/sync` で新着なし時に `work` へ自動移行する設計がシームレス

**改善余地:**

- DeepResearch 蓄積カウントのリセット条件（`tags に DeepResearch を含む最新メッセージ`）が実装上判定しにくい可能性がある

### 4. Windows 互換性

**発見された問題と対応:**

| 問題 | 影響 | 対応状況 |
| ---- | ---- | -------- |
| 変数定義が `VAR=value`（sh 構文）のみ | PowerShell で変数定義が失敗する | ✅ PowerShell 版を `subcommands.md` に追加 |
| `cd $WORKTREE_PATH` | PowerShell では動作する（問題なし） | — |
| `git -C $WORKTREE_PATH <cmd>` | PowerShell でも動作する | — |
| `hostname` コマンド | Windows でも動作する | — |
| パス区切り文字 `/` | Git for Windows は `/` を受け入れる | — |

**troubleshoot サブコマンドの Windows 対応:**

`subcommands.md` の「調査深度の基準」と「典型パターン」には Linux/macOS と Windows/PowerShell の両対応コマンドが既に記載されており、対応は十分。

**残課題:**

- Commit & Push のコードブロックが `sh` タグのまま。PowerShell 版を併記するとより親切だが、`git` コマンド自体は PowerShell でもほぼ同一のため優先度は低い

### 5. GitHub Copilot 互換性

**結論: このスキルは Claude Code 専用であり、Copilot では動作しない。**

理由:

1. **自律的なシェルコマンド実行**: `git worktree add`、`git push`、`hostname` などを AI が自律的に実行する機構が Copilot にはない
2. **スキル依存**: `deep-research` スキルは Claude Code のスキルシステムに依存しており、Copilot には存在しない
3. **ファイル自律操作**: messages/ ディレクトリへのファイル作成・コミット・プッシュを自律実行する機能が Copilot にはない

**Copilot で手動利用する場合:**

`references/subcommands.md` を手順書として参照し、各ステップをユーザーが手動実行すれば機能させることは可能。

---

## 推奨事項

### 高優先度（即対応済み）

- [x] `subcommands.md` に PowerShell 変数定義を追加
- [x] `SKILL.md` に「プラットフォーム互換性」セクションを追加（Windows/Copilot 両方を記載）

### 中優先度（今後の改善候補）

- [ ] `git push` 失敗時のリトライ手順を追加（特に Windows 環境でのネットワーク断対策）
- [ ] `deep-research` スキルが未インストールの場合のフォールバック手順を記載

### 低優先度

- [ ] Commit & Push セクションに PowerShell 版コードブロックを併記
- [ ] `check` サブコマンドに「pull なしの読み取り専用モード」オプションを検討
