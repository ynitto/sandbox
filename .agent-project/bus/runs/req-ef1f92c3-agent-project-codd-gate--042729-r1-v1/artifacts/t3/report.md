# t3: intake（enqueue/acceptance）結線

## (a) 成果

対象は実体コードのあるメイン worktree `/Users/nitto/Workspace/sandbox`（`.agent-project` は
sparse な制御面で `.agent/` 自体を持たない。[[agent-project-verify-location]] の通り）。

`.agent/agent-project.yaml` の `regression_cmd`/`intake_cmd` 静的配線は既に別タスク（前 run の
t3）で完了済みで、完了条件は着手前から exit 0 だった:

```yaml
regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'
intake_cmd: 'codd-gate tasks --debt --repos .agent-project/repos.json'
```

本タスクの実質スコープは、静的なコマンド文字列の先にある**実行時の enqueue 経路**。
`tools/agent-project/codd_gate_debt.py`（`codd-gate tasks --debt` 出力をレコード単位で検証・
正規化する既存モジュール）が、自身のdocstringで明示的に「未実装」と宣言していた統合ギャップ
（`agent-project.py への結線・cfg.intake_cmd/run_intake との統合、id ベースの冪等排除`）を埋めた。

変更ファイル（すべて `/Users/nitto/Workspace/sandbox` 配下）:

1. **`tools/agent-project/agent_project/model.py`**（`run_intake`）
   - `_codd_gate_debt_module()` を追加: `codd_gate_debt`（sibling module）を遅延 import する。
     `__init__.py` の exec 合成方式のもとでは `__file__` が常に `agent_project/__init__.py` の
     実パスを指すため（`instances.py` の `_self_script` と同じ前提）、その1階層上
     （`tools/agent-project/`）を sys.path に足してから import する。
   - `run_intake` 本体: import に成功すれば `debt.parse_debt_output(out)` でレコード単位の検証
     （非 object・title 欠落を1件ずつ弾く）を通し、`result.errors` を journal へ、
     `result.items` を `DriftItem.to_spec()` で spec dict に戻して以降の
     enqueue（id 冪等排除・`enqueue_task`）へ流す。**1件の不備で他のレコードを巻き込まない**
     （codd-gate 側が「ある回は1件だけ title 欠落」を返しても残りは取り込まれる）。
     import 不可（sibling module 欠落）なら従来の緩いパース（非 dict を黙って読み飛ばす）へ
     no-op 縮退し、既存挙動を完全維持する（`codd_gate_status` の usable=False 縮退と同じ方針）。
   - id ベースの冪等排除（`existing` backlog stem 集合との突合）は変更前と同じロジックのまま。

2. **`tools/agent-project/codd_gate_debt.py`**（docstring のみ）
   - 「意図的に含めないもの」節から、今回実装した e2（model.py への結線）を除去し、
     実装場所（`model.py` の `run_intake`/`_codd_gate_debt_module`）への参照に更新。

3. **`tools/agent-project/tests/test_codd_gate_debt.py`**（新規）
   - `parse_debt_output`/`DriftItem.to_spec` の単体テスト10件
     （空/空白/単一object/配列/非JSON/1件不備で残りを保持/id欠落許容/未知フィールド保持/
     to_spec の往復）。既存 `test_codd_gate_routing.py`/`test_codd_gate_detect.py` と同じ
     `sys.path.insert(parent) → import codd_gate_debt as debt` の規約に合わせた。

4. **`tools/agent-project/tests/test_agent_project.py`**（`TestIntake` に1件追加）
   - `test_run_intake_one_bad_record_does_not_block_the_rest`: `codd-gate tasks --debt` 形の
     配列で1件が title 欠落でも、残りが enqueue され journal に理由が残ることを確認
     （model.py 側の統合の end-to-end 検証）。

## (b) 検証内容と結果

- `python3 -m unittest discover -s tools/agent-project/tests` を `/Users/nitto/Workspace/sandbox`
  で実行 → **706 件中 705 pass**。唯一の失敗 `test_kf_base_passes_flow_config` は
  `/var` vs `/private/var`（macOS の tmp シンボリックリンク解決差）由来の環境依存 flake で、
  `flow_config`/`_kf_base` の話であり本タスクの変更（`run_intake`/`codd_gate_debt`）と無関係
  （diff にも触れていない）。事前（変更前 baseline）から存在する既知の環境差と判断。
- 新規テスト単体: `python3 -m unittest tests.test_codd_gate_debt tests.test_agent_project.TestIntake -v`
  → 全 pass（`TestIntake` 6件・`test_codd_gate_debt` 10件）。
- 実エントリポイント経由の import 疎通確認:
  `python3 tools/agent-project/agent-project.py --help` が正常終了、かつ
  `import agent_project as km; km._codd_gate_debt_module()` が
  `tools/agent-project/codd_gate_debt.py` を正しく解決することを確認（テストの
  `importlib.util.spec_from_file_location` 経由ロードとは別の sys.path 経路でも動くことの確認）。
- 完了条件（regression_cmd）再確認: `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml` → exit 0（着手前から満たされていた分。本タスクでは変更していない）。
- `intake_cmd` の対応する疎通確認（本タスクの直接対象。完了条件には入っていないが整合性として）:
  `grep -E '^[[:space:]]*intake_cmd:.*codd-gate tasks' .agent/agent-project.yaml` → exit 0。
- `yaml.safe_load('.agent/agent-project.yaml')` でロード可能・両キーの値が期待通りであることを確認
  （読み取りのみ。本タスクではこのファイルを編集していない）。

## (c) 採用した前提・未解決事項・範囲外

- **前提**: 実体コード・テストは `/Users/nitto/Workspace/sandbox`（メイン worktree）にあり、
  完了条件の grep もそちら基準で評価される（[[agent-project-verify-location]] の記録どおり）。
  `.agent-project`（本 worktree）は sparse な制御面としてのみ扱った。
- **前提**: タスク文中の「acceptance」は charter の `acceptance`（受入 verify）機能とは別物とみなした。
  `codd_gate_status.py`/`codd_gate_routing.py` の docstring が定義する「regression/acceptance/enqueue
  の3フック」の acceptance は本タスクの範囲外（t1/t2 側が持つ検出・regression 結線の話）とし、
  本タスクは「intake（enqueue）」= `codd-gate tasks --debt` → `run_intake` → backlog enqueue の
  経路配線に絞った。
- **範囲外で見つけた問題（直していない）**: `tools/agent-project/install.sh` の zipapp 生成は
  `agent_project/` パッケージ配下の `*.py` しかコピーしておらず（41-45行目）、
  `codd_gate_debt.py`/`codd_gate_status.py`/`codd_gate_detect.py`/`codd_gate_routing.py`
  （いずれも `tools/agent-project/` 直下の sibling module）を含んでいない。開発木（このリポジトリ内で
  `agent-project.py`/パッケージを直接実行）では問題なく動くが、`install.sh` で生成した配布用
  zipapp では sibling module が同梱されないため import に失敗し、`_codd_gate_debt_module()` は
  None を返して（no-op 縮退）緩いパースにフォールバックする＝配布バイナリでは
  codd-gate 連携の恩恵（レコード単位検証）が効かない。この4ファイルすべてに関わる横断的な
  bundling の話で、t1/t2（検出・regression 結線）側の module も同じ影響を受けるため、本タスク
  単独では直さず評価役の判断に委ねる（同じファイル `install.sh` を複数タスクが同時に触ると
  共有 worktree 上で衝突するリスクもあるため）。
- 未解決事項なし（本タスクのスコープ内は完了）。
