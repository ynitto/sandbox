# t1: codd-gate 連携の契約確定（調査結果）

対象コード（実体）は `/Users/nitto/Workspace/sandbox`（backlog の `workspace: sandbox`）。
本タスクは調査・契約確定のみで、そちらへの書き込みは行っていない。

## (a) 成果 — 確定した契約

### 1. `detect_status()` の戻り値型

`tools/agent-project/codd_gate_status.py` に実装済み（前ラウンド r0 由来、`a1/a4/d2` 成果）。

```python
@dataclass(frozen=True)
class CoddGateStatus:
    binary: "list[str] | None"
    version: "tuple[int, int, int] | None" = None
    findings: "list[dict]" = field(default_factory=list)

    @property
    def usable(self) -> bool: ...        # binary is not None and not findings
    def command(self, *args: str) -> "list[str] | None": ...
    @property
    def reason(self) -> str: ...         # findings[0]["title"] または ""

def detect_status(explicit: "str | None" = None, which=shutil.which) -> CoddGateStatus: ...
```

- `detect_status()` は例外を投げない。codd-gate 未検出・非互換のあらゆる経路は
  `usable=False` の `CoddGateStatus` に縮退する（no-op 縮退。findings が1件でもあれば usable=False）。
- 後続タスクが依存してよいのは `.usable` と `.command(...)` の2点のみ。`findings`/`reason` の
  中身（文言・件数）には依存しないこと（診断用途）。

### 2. `command(*args)` の返却形式

- 実シグネチャは `command(self, *args: str) -> list[str] | None`（"sub" 専用引数は無い。
  第1引数を慣習的にサブコマンド名にする——例 `status.command("verify", "--base", "HEAD")`）。
- 返り値は `[*self.binary, *args]`（=検出済み起動 argv prefix + 渡した引数をそのまま連結した
  `list[str]`）。`usable=False` なら常に `None`。
- `binary`（`codd_gate_detect.resolve_codd_gate` が解決する argv prefix）は
  PATH 上に `codd-gate` があれば `["codd-gate"]`、無ければ同梱パス
  `tools/codd-gate/codd-gate.py` を `[sys.executable, "<path>"]` で起動する2値のどちらか。
- 呼び出し側の定型は `if status.command(...):` の1行分岐のみで済む設計
  （`subprocess.run(status.command("verify", "--base", rev, *routing_args, "--strict"))` 等）。

### 3. yaml キーのインデント幅

`.agent/agent-project.yaml`（sandbox リポジトリ）は現状 `regression_cmd`/`intake_cmd` を
**まだ持たない**（root/agent_cli/model/agents の4トップレベルキーのみ）。よって「既存キー位置」は
実ファイルではなく `agent-project.yaml.example`（コメントアウトの参照実装）と
`agent_project/configfile.py` の DEFAULTS / `config.py` の dataclass 定義から確定した。

- 両キーとも **フラットなトップレベルキー**（インデント0）。`agents:` ブロックの子キーのような
  2スペースインデントの対象では**ない**（`config.py`/`configfile.py` に両者ともネストなしで
  定義されているため）。
- `agent-project.yaml.example:171,179` の参照値（インデント0、`# ` コメントアウト）:
  ```yaml
  # regression_cmd: make -s smoke   # done 確定前のグローバル回帰検査（失敗で done にせず人へ）
  # intake_cmd: codd-gate tasks --debt
  ```
- codd-gate 連携時の正準値は `README.md:234-235` に明記済み:
  ```yaml
  regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos <root>/repos.json'
  intake_cmd: 'codd-gate tasks --debt --repos <root>/repos.json'
  ```
  （`<root>` は agent-project が charter から自動生成する repos.json の実際の相対/絶対パスに置換。
  パス解決自体は `codd_gate_routing.resolve_repos_arg` が実装済み。）
- 完了条件の grep は `^[[:space:]]*regression_cmd:.*codd-gate verify --base` /
  `^[[:space:]]*intake_cmd:.*codd-gate tasks` なので、インデント0で上記の値をそのまま
  `.agent/agent-project.yaml` に追記すれば満たす。

## (b) 検証内容と結果

- `codd-gate --help` / `verify --help` / `tasks --help` / `--version` を実行し、実サブコマンド仕様を確認（`/Users/nitto/Workspace/sandbox` で実行、読み取りのみ）。
  - `verify`: `--repos FILE --config CONFIG --repo-dir NAME=DIR --sync --map --json --base BASE --repo REPO --strict --strict-cross --debt --max-broken/--max-undocumented/--max-untested`
  - `tasks`: `--repos --config --repo-dir --sync --map --json --base --repo --debt --priority --max --cohort --inbox`
  - version: `codd-gate 1.0.0`（`codd_gate_status.MIN_SUPPORTED_VERSION = (1, 0, 0)` を満たす）
- `PYTHONPATH=tools/agent-project python3 -m pytest tools/agent-project/tests/test_codd_gate_detect.py tools/agent-project/tests/test_codd_gate_routing.py -q` → **29 passed**（sandbox リポジトリ、既存実装に対して実行。今回の調査で変更は加えていない）。
- `git status --short tools/agent-project/ .agent/`（sandbox）→ 差分なし。`codd_gate_status.py`/`codd_gate_detect.py`/`codd_gate_routing.py`/`codd_gate_base.py`/`codd_gate_debt.py` と対応テストは前ラウンド（r0）で実装・コミット済みであることを確認。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 対象コードは backlog の `workspace: sandbox` に従い `/Users/nitto/Workspace/sandbox` とした（本 worktree `.agent-project` 配下には `tools/agent-project` が存在しないため）。本タスクは調査・契約確定のみで sandbox 側への書き込みは行っていない（範囲外）。
- **未解決事項**: `.agent/agent-project.yaml` への `regression_cmd`/`intake_cmd` の実追記、および `agent_project/mr.py`（regression フック）・`agent_project/model.py`（intake フック）・enqueue 経路（acceptance）への `codd_gate_status`/`codd_gate_routing`/`codd_gate_base` の自動配線（設計上の b3/c1/e1）は未着手。後続タスク（t2/t3/t4 等）の担当と見られる。
- **範囲外で見つけた問題**: `t2` のゴール文面は「`codd_gate_status.py` を実装する」だが、そのファイルは前ラウンドで実装済み・テスト29件PASS済みで、実質的に重複する可能性がある（評価役の判断事項として報告のみ）。
