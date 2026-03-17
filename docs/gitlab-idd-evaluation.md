# gitlab-idd スキル 評価レポート

評価日: 2026-03-17

---

## テスト結果サマリー

| カテゴリ | テスト数 | 結果 |
|---------|---------|------|
| 構文チェック（3スクリプト） | 3 | ✅ 全合格 |
| `gl.py` ユーティリティ関数 | 10 | ✅ 全合格 |
| `check-defer` ロジック | 3 | ✅ 全合格 |
| ポーリングデーモン | 6 | ✅ 全合格 |
| セットアップスクリプト | 6 | ✅ 全合格 |
| テンプレート変数整合性 | 2 | ✅ 全合格 |

---

## スクリプト品質評価

### `scripts/gl.py`

**良い点:**
- stdlib のみで動作（外部依存ゼロ）
- `get_project_info` が SSH / HTTPS / HTTPS+token URL をすべて正しくパース
- `extract_field` のドット記法が配列インデックス・ネストオブジェクトを正しく処理
- `update-issue` がラベルの read-modify-write を正しく実装（差分更新）
- `check-defer` の3ケース（他者発行 / 自分発行猶予中 / 自分発行猶予切れ）が正確に動作
- argparser: 13 サブコマンドすべて正常パース
- `GITLAB_SELF_DEFER_MINUTES` 環境変数からデフォルト値を読み取る動作が正常

**発見した軽微な問題:**
1. **ドキュメント不整合** (`references/gitlab-api.md` L56): コメントが `# → false`（小文字）だが実際の Python 出力は `True`/`False`（大文字）。Bash 比較コード（L191）は `"True"` で正しく書かれているため動作に影響はないが、コメントが誤解を招く。
2. **ページネーション未対応**: `list-issues` は `per_page=100` のみで、100件超のプロジェクトでは全件取得されない。現実的なワークフローでは問題になる可能性は低い。

### `scripts/gl_poll_daemon.py`

**良い点:**
- `build_worker_prompt` が `string.Template.safe_substitute` を使い、未定義変数をそのまま残す安全な実装
- テンプレートディレクトリの優先順位（設定ディレクトリ → スキルリポジトリ → フォールバック文字列）が明確
- `mark_seen` の冪等性が正しく実装
- `save_config` がアトミックな tmp→rename で書き込み（データ破損防止）
- config.json のファイルパーミッション `0600` 設定（トークン保護）
- `seen_issues` をソート済みリストで保存（JSON diff が安定）
- `--dry-run` / `--once` / `mock_cli` の3モード切り分けが明確
- WSL kiro 向けの専用テンプレートとコマンドビルダが分離

**発見した軽微な問題:**
1. **ファイルハンドル**: `launch_agent_worker` 内で `open(log_file, "w")` を `Popen(stdout=...)` に直接渡しており、Python 側でファイルを close() していない。OS が後でクリーンアップするため動作上は問題ないが、長期稼働のデーモンでは潜在的なハンドルリーク。

### `scripts/gl_poll_setup.py`

**良い点:**
- `configure_session_hook` が冪等（重複登録しない）
- `add_repo_to_config` が新規追加・重複スキップ・local_path 更新を正しく処理
- `--dry-run` が全破壊的操作（ファイル書き込み・OS サービス登録）に対して有効
- `--set KEY=VALUE` が型変換と入力バリデーションを実施
- macOS (launchd) / Linux (systemd → crontab fallback) / Windows (タスクスケジューラ) の3OS対応

---

## テンプレート整合性

`worker-prompt.md` と `worker-prompt-wsl-kiro.md` で使われる全変数が `build_worker_prompt` の `variables` 辞書で定義済みであることを確認。未定義変数によるプロンプト汚染なし。

---

## リファレンス品質評価

| ファイル | 評価 |
|---------|------|
| `gitlab-api.md` | 実装との整合性高。コマンド例が実際のパーサと一致。L56のコメント軽微な誤記あり |
| `worker-role.md` | フロー手順・bash スクリプト例が実装と整合 |
| `requester-post.md` | 簡潔で明確。イシューテンプレートが実用的 |
| `requester-review.md` | 並列評価の指示が明確。判定基準テーブルが実用的 |
| `polling-daemon.md` | `--dry-run` vs `mock_cli` の違いの表が特に有用 |

---

## 総合評価

**総合: ✅ 安定版として品質基準を満たす**

コア機能（イシュー作成・取得・ラベル管理・MR操作・check-defer）はすべて正常動作。スクリプトの設計は堅牢で、stdlib のみの依存・クロスプラットフォーム対応・冪等性・アトミックなファイル書き込みなど、実運用に耐える実装品質。発見した問題はいずれも軽微で動作には影響しない。
