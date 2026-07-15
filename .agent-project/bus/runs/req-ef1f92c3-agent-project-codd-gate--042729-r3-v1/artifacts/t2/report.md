# t2: regression_cmd の結線

## 完了条件

```
grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml
```

終了コード 0 で成立（下記「検証」参照）。

## 成果・サマリー

`.agent/agent-project.yaml:30` にはタスク着手前から次の行が既存だった。追加の編集は不要と判断し、内容の正しさを裏取りするのみに留めた。

```yaml
regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'
intake_cmd: 'codd-gate tasks --debt --repos .agent-project/repos.json'
```

- t1 が確定した実バイナリの呼び出し形（`codd-gate verify --base <ref> --repos <repos.json>`）と一致。
- ツール本体のリポジトリ（`/Users/nitto/Workspace/sandbox/tools/agent-project/README.md:234`）に記載の正典テンプレート `regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos <root>/repos.json'` と完全一致（`<root>` を実値 `.agent-project` に展開した形）。
- 重複行・綴り違い（`regresion_cmd` 等）は存在しないことを確認済み。
- README によれば `<root>/repos.json` が実在すれば起動時の自動検出（`build_config`）が未設定時のみメモリ上で埋めてくれる仕様だが、今回は既に手書きされているため自動検出の対象外（自動生成は「既存の手書き値を上書きしない」）。手書きのままで README の推奨形と一致しているので変更不要と判断した。

## 検証内容と結果

```bash
$ grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml
regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'
$ echo $?
0
```

完了条件のシェルコマンドが終了コード 0 で成功することを確認した。

## 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 完了条件は「ファイルに当該行が存在し grep が成立すること」のみを要求しており、`codd-gate` の実行そのもの（end-to-end 疎通）は本タスクの範囲外と解釈した。
- **未解決事項（範囲外）**: `.agent-project/repos.json` が未生成のため、`regression_cmd` を実際に叩く end-to-end 検証は依然として後続タスクの範囲（t1 の報告と同じ未解決点が継続）。
- **範囲外で見つけた問題**: なし（重複・誤字なし、README 正典と完全一致）。
