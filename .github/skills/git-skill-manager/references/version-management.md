# バージョン管理操作

## changelog

スキルのコミット履歴とフロントマターのバージョン変更から `CHANGELOG.md` を自動生成する。

→ 実装: `scripts/changelog.py` — `generate_changelog(skill_name)`、`scripts/manage.py` — `changelog_skill(skill_name, dry_run)`

```bash
python changelog.py <skill_name>               # CHANGELOG.md を生成・上書き
python changelog.py <skill_name> --dry-run     # 内容を標準出力のみに出力
```

---

## bump

SKILL.md の `metadata.version` をセマンティックバージョニングに従ってインクリメントする。
バージョンは `X.Y.Z` 形式（メジャー.マイナー.パッチ）で管理する。

### セマンティックバージョニング指針

| 変更の種類 | バージョン | 例 | 説明 |
|---|---|---|---|
| バグ修正・誤字修正・軽微な表現改善 | **patch** | `1.2.3 → 1.2.4` | 動作に影響しない小さな修正 |
| 後方互換の機能追加・手順の強化 | **minor** | `1.2.3 → 1.3.0` | 既存ユーザーへの影響なし |
| 破壊的変更・大幅な動作変更 | **major** | `1.2.3 → 2.0.0` | 既存ユーザーが対応を要する変更 |

### 処理フロー

→ 実装: `scripts/manage.py` — `bump_version(skill_name, bump_type)`、`scripts/registry.py` — `_update_frontmatter_version(skill_path, new_ver)`

1. ワークスペース → インストール済みの順でスキルを検索
2. SKILL.md の `metadata.version` を読み取り、bump_type に応じてインクリメント
3. SKILL.md の version フィールドを書き換え
4. レジストリの `version` / `version_ahead` を更新

```
「react-frontend-coder のバージョンを上げて」

エージェント:
  1. bump_type を確認（patch / minor / major）
  2. python manage.py bump react-frontend-coder patch
  3. スキルを修正してから python changelog.py react-frontend-coder を実行するよう案内
  4. 完了後、push または promote を提案
```

### バージョンアップのタイミング

- **スキル改良（refine）完了後**: 内容に応じて patch または minor を bump する
- **push / promote 前**: バージョンを上げてから push することで、利用者が更新を認識しやすくなる
- **changelog 生成前**: bump してからコミットし、changelog を生成するとセクションが正しく区切られる

---

## discover

`skill-creator`（モードC: 履歴から生成）を起動し、直近のチャット履歴から新しいスキル候補を発見する。

### 処理フロー

1. ユーザーに `--since` パラメータ（分析開始日時）を確認
2. ユーザーに同意を確認:
   ```
   「指定期間のチャット履歴を分析して新しいスキル候補を探します。
    続行しますか？」
   ```
   （ここで同意を取得済みのため、`skill-creator` モードC（references/generating-skills-from-copilot-logs.md）の Phase 1 同意確認はスキップしてよい）
3. `skill-creator`（モードC）サブエージェントを起動してフェーズ 2〜6 に従って分析・スキル生成（Phase 1 の同意確認は不要）
