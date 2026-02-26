# エージェントへの共通指示

## セッション開始時のスキル自動更新チェック

セッション開始時に、git-skill-manager の自動更新チェックを実行する:

```bash
python .github/skills/git-skill-manager/scripts/auto_update.py check
```

`--force` なしの場合、`interval_hours` 未満であればスキップされる（ネットワーク負荷を抑制）。
git-skill-manager がインストールされていない環境ではスキップしてよい。

-----

## スキル実行後のフィードバック収集

スキルを単体で実行完了したら、**scrum-master 経由の場合を除き**、
git-skill-manager の `feedback` 操作でフィードバックを収集する。

```
git-skill-manager で [スキル名] のフィードバックを記録して
```

scrum-master 経由の場合はスプリント終了時に一括収集されるためスキップする。
git-skill-manager がインストールされていない環境ではスキップしてよい。

-----

## スキルの優先順位

同名のスキルが `~/.copilot/skills/`（ユーザーホーム）と `.github/skills/`（ワークスペース）の
両方に存在する場合、**ユーザーホーム側を優先して使用する**。
ワークスペース側のバージョンは無視してよい。
