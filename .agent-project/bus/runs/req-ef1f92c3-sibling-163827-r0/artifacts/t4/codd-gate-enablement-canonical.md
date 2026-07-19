# codd-gate 有効化手順の正準（t4 で README へ確定させた内容）

GUIDE.md / 設計書 / SKILL.md を追随させる後続タスクは、以下の3点を正とすること。
README.md「フレーク耐性 / 回帰 / 検収 / パス保護」節の `一貫性ゲート（codd-gate 連携・オプション）` 箇条が実体。

## 1. 経路は2つ（どちらも人が起点。起動時に勝手に入るものは無い）

**(a) yaml 直書き** — `.agent/agent-project.yaml` に2行:

```yaml
regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos <root>/repos.json'
intake_cmd: 'codd-gate tasks --debt --repos <root>/repos.json'
```

任意で charter acceptance に `codd-gate verify --debt --max-broken N …`（受入の負債ラチェット）。

**(b) CLI 注入** — `regression_cmd` の1行だけ:

```sh
python3 codd_gate_regression.py --config .agent/agent-project.yaml [--repos <path>] [--base <val>] [--dry-run]
```

`intake_cmd` に対応する注入 CLI は存在しない（yaml を直接編集する）。

## 2. CLI の実測挙動（実行して確認済み）

| 項目 | 実測値 |
|---|---|
| `--repos` 省略 | 設定の `root:` から `<root>/repos.json` を推定（`root:` 不在なら `.agent-project/repos.json`） |
| `--dry-run` | ファイル未変更。JSON `{usable, reason, cmd, changed, config, dry_run}` を stdout へ |
| 生成される値 | `codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json`（yaml 直書きの推奨値と同一文字列） |
| 新規挿入位置 | `intake_cmd:` の直前 → 無ければ `agent_cli:` の直前へ見出しコメント付きブロック → どちらも無ければ末尾 |
| 再実行 | 同値なら `changed=false` で書き込み自体を省略（mtime も触らない） |
| codd-gate 未検出・非互換 | 何も書かない（`build_regression_cmd` が None → `upsert_config_text` が no-op） |

## 3. 確認手段は `doctor`

`doctor_wiring_findings`（`agent_project/doctor.py:313`）が能力フック `wiring.detect` / `wiring.findings` で
`codd_gate_wiring` を引き当て、codd-gate を検出できたのに `regression_cmd`/`intake_cmd` が
それを指していなければ、貼れる推奨コマンド文字列を severity=info の所見として出す。

**注意（旧 README の誤りを t4 で訂正済み）**: 検出の発火条件は「`<root>/repos.json` が実在すること」では**ない**。
`detect_wiring`（`codd_gate_wiring.py:139`）は repos.json の有無に関わらず走り、実在するときだけ
schemas 契約の互換判定（`check_repos_schema_compat`）が1段増える。

## 4. 書いてはいけないこと

`build_config` によるメモリ上の自動配線は実装から除去済み。README からもその説明を削除した
（`tests/test_agent_project.py` の `TestCoddGateNoAutoWiring` が再導入を禁じる回帰ガードとして残る＝
`_apply_codd_gate_auto_wiring` の名前はテスト側にだけ存在してよい）。
追随ドキュメントで「起動時に自動で結線される」旨を書き足さないこと。
