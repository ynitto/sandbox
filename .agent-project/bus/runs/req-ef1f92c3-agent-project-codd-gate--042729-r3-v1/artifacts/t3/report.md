# t3: intake_cmd 結線（負債修復タスクの intake）

## (a) 成果 / サマリー

完了条件は着手前から既に成立していた。`.agent/agent-project.yaml:31` に

```yaml
intake_cmd: 'codd-gate tasks --debt --repos .agent-project/repos.json'
```

が既存で、以下の三者と一致することを確認した。

- 依存タスク t1 の裏取り結果（README 正典）: `codd-gate tasks --debt --repos <root>/repos.json`
- `tools/agent-project/README.md`（230–244行、t1 report 参照）記載の正しい呼び出し形
- 先行 run（r0）の投入時に決めたトップレベルキー配置（インデント0、`regression_cmd` の直下）

`regression_cmd:`（30行目）も既に正しく設定済みで、本タスクの完了条件（regression_cmd の grep）・隣接する intake_cmd 側の grep の両方が exit 0 になる状態。今回コード・設定ファイルへの追加変更は行っていない。

## (b) 検証内容と結果

```
$ grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml
regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'
exit_code=0
```

（本タスクの完了条件そのもの。成立を確認済み。）

```
$ grep -E '^[[:space:]]*intake_cmd:.*codd-gate tasks --debt' .agent/agent-project.yaml
intake_cmd: 'codd-gate tasks --debt --repos .agent-project/repos.json'
exit_code=0
```

（本タスクの主題である intake_cmd 側。r0 の verify-gate が使った `grep -E '^[[:space:]]*intake_cmd:.*codd-gate tasks'` 相当のパターンでも exit 0 を確認済み。）

さらに、`model.py` の `run_intake` / `config.py` の `Config` が参照するフィールド名 `intake_cmd`（r0 t1 investigation-memo で特定済み）と綴りが一致していること、`"codd-gate" in cfg.intake_cmd` という早期判定（r0 t3 で追加された codd-gate 未導入時のガード）が値に対して真になることを、値の文字列を目視で突き合わせて確認した。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: `.agent/agent-project.yaml` は `git status` 上 untracked（新規ファイル扱い）だが、これは commit 0053060f「同期除外パスを追跡から外す（自己修復）」により意図的に git 追跡対象から外された設定ファイルであり、ディスク上の内容自体は物理 worktree に永続している。今回の r3-v1 run が既存の正しい値をそのまま引き継いでいると解釈し、値を破壊・再生成しなかった。
- **未解決事項（範囲外）**: `<root>/repos.json`（`.agent-project/repos.json`）が実在しないため、`intake_cmd` を実際に shell 実行して `codd-gate tasks --debt` が成功する end-to-end 検証は本タスクの範囲外（t1 report と同じ結論）。この生成・結線は後続タスクの担当と判断する。
- **範囲外で見つけた問題**: なし（本タスクのスコープ内で新規に発見した不整合はない）。
