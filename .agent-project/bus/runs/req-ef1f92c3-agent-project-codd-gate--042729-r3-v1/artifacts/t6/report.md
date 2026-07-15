# t6 統合: regression/intake 結線の裁定と修正適用

## 裁定

3系統（t2, t3 vs t4, t5）の結論は矛盾していた。**t4/t5 の実測結果を採用し、t2/t3 の「README 文面と一致するので正しい」という結論を却下した。**

- t2/t3: `.agent/agent-project.yaml:30-31` の `regression_cmd`/`intake_cmd` は README 正典
  （`tools/agent-project/README.md:234`）の `--repos <root>/repos.json` という記述と文字面が
  一致するため「正しい」と判定。実行時の裏取りはしていない。
- t4/t5: 実行 cwd（`cfg.workdir`）で実際にコマンドを走らせ、`--repos .agent-project/repos.json`
  が cwd 基準で二重パスになり `codd-gate` が exit 2（repos レジストリ未検出）で即失敗することを
  再現。あわせて `--repo-dir` 引数が欠落していることも指摘。

**却下理由**: README の `<root>/repos.json` はプレースホルダであり、`root:` 設定値
（`.agent-project`）をそのまま文字列連結してよいという意味ではない。regression_cmd/intake_cmd は
`cfg.workdir`（= root ディレクトリそのもの）を cwd として実行されるため、cwd 基準では
`repos.json`（相対パスなし）が正しい。t2/t3 は「文書と一致するか」だけを見て「実行して動くか」を
検証しておらず、静的な文字列比較を実行可能性の証明として扱った誤りがある。

さらに `tools/agent-project/codd_gate_routing.py` の `resolve_repos_arg`/`build_routing_args`
（未結線時の自動推奨コマンドを組み立てる設計正典）を確認し、推奨形は常に
`--repos <vcwd相対パス> --repo-dir <repos.jsonのエントリ名>=<dir>` であることを裏取りした。
`repos.json` の実エントリは `{"src": {...}}` の1件のみのため、`--repo-dir src=.` が該当する。

## 適用した修正

`.agent/agent-project.yaml:30-31` を書き換えた:

```diff
- regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'
- intake_cmd: 'codd-gate tasks --debt --repos .agent-project/repos.json'
+ regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json --repo-dir src=.'
+ intake_cmd: 'codd-gate tasks --debt --repos repos.json --repo-dir src=.'
```

あわせて直上のコメントに、cwd が root 自身になるため `<root>/repos.json` を文字通り書くと
二重パスになる旨と、`--repo-dir` の由来（`codd_gate_routing.py`）を追記した。

## 機械検証（実測）

1. 完了条件コマンド（本タスクの必須ゲート）
   ```
   grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml
   ```
   → **exit 0**

2. 隣接する intake の結線も同形式で確認
   ```
   grep -E '^[[:space:]]*intake_cmd:.*codd-gate tasks --debt' .agent/agent-project.yaml
   ```
   → exit 0

3. yaml に書いた文字列をそのまま実行 cwd（このディレクトリ = `cfg.workdir`）で走らせた end-to-end 再現
   - `KIRO_BASE_REV=HEAD bash -c 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json --repo-dir src=.'`
     → repos レジストリ解決に成功し実差分スキャンが走った（exit 1 = codd-gate 自身の「ドリフトあり」
     判定であり、結線・レジストリ解決の失敗ではない。t4/t5 と同じ整理）
   - `codd-gate tasks --debt --repos repos.json --repo-dir src=.`
     → **exit 0**。修復タスク候補の JSON を正常出力
   - 修正前の文字列（`--repos .agent-project/repos.json`）を同じ cwd で再実行し、
     `[codd-gate] エラー: repos レジストリが見つかりません` / exit 2 を再現。修正差分が
     原因を解消したことを対照確認した。

## 未解決事項（範囲外として明記）

- `codd-gate verify` 自体は「ドリフトあり」（AMBER 多数）で exit 1 を返す。これは repos.json の
  結線が機能した結果として codd-gate が正しく差分を検出しているだけであり、本タスク（結線の
  有効化）のスコープ外。ドリフトの是正は別タスクの責務。
- `repos.json` は現状エントリが `src` の1件のみ。今後リポジトリが増える場合は `--repo-dir` に
  エントリを追記する運用になる（`codd_gate_routing.build_routing_args` は複数エントリを
  自動では展開しない。静的文字列である現行の `regression_cmd`/`intake_cmd` も同様に手動追記が必要）。
- `codd_gate_wiring.py`/`codd_gate_detect.py`/`codd_gate_routing.py` の自動検出・推奨コマンド
  生成ロジックは実装済みだが、`.agent/agent-project.yaml` への実書き込みや `cfg.regression_cmd`
  の動的組み立てへの置き換えはモジュール docstring 上も明示的にスコープ外とされている
  （静的文字列のまま動く前提を崩さない設計）。今回は静的文字列側を手直しする対応で完了条件を
  満たした。

## 成果物

- `.agent/agent-project.yaml`（regression_cmd/intake_cmd 修正・コメント補強）
- 本レポート: `bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r3-v1/artifacts/t6/report.md`
