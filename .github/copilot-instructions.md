# エージェントへの共通指示

## スキル実行後のフィードバック収集

スキルを単体で実行完了したら、**scrum-master 経由の場合を除き**、
git-skill-manager の `feedback` 操作でフィードバックを収集する。

```
git-skill-manager で [スキル名] のフィードバックを記録して
```

scrum-master 経由の場合はスプリント終了時に一括収集されるためスキップする。
git-skill-manager がインストールされていない環境ではスキップしてよい。
