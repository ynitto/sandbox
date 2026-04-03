# snapshot / rollback 詳細

## 手動操作

実装: `scripts/snapshot.py`

```bash
python snapshot.py save --label "v2移行前"              # ラベル付きで手動保存（上限 10 件を自動適用）
python snapshot.py save --label "移行前" --max-keep 5   # 上限件数を指定して保存
python snapshot.py list                                 # スナップショット一覧
python snapshot.py restore --latest                     # 直近のスナップショットに戻す
python snapshot.py restore snapshot-20260227T103000      # 指定スナップショットに戻す
python snapshot.py clean --keep 3                       # 最新 3 件のみ保持（手動クリーンアップ）
```

## スナップショットの上限管理

`save_snapshot()` は保存後に `max_keep`（デフォルト: 10）を超えた古いスナップショットを自動削除する。スナップショットが溜まり続けることはない。

- `--max-keep` を省略した場合、デフォルト上限の 10 件が適用される
- 手動で一括クリーンアップしたい場合は `clean --keep N` を使う

## スナップショットの保存内容

```
<AGENT_HOME>/snapshots/snapshot-{timestamp}/
    ├── meta.json           # 作成日時・ラベル・スキル一覧
    ├── skill-registry.json # レジストリの完全コピー
    └── skills/             # <AGENT_HOME>/skills/ の完全コピー
```
