# loop（verify）— 完了条件 grep 群の判定

判定: **pass**（反復ゼロ。文言修正は不要で作業ツリーは未改変）

## 実行した完了条件（backlog `codd-gate-163827` の verify 原文そのまま）

```
grep -nE 'agent_project.*(import|結合|依存).*(しない|外|禁止)|パッケージ.*(codd_gate|sibling)|有効化は設定' tools/agent-project/README.md \
&& grep -nE 'regression_cmd|intake_cmd|codd_gate_\*\.py|自動検出' tools/agent-project/README.md \
&& test -f docs/designs/codd-gate-design.md \
&& grep -nE 'agent_project パッケージ|_apply_codd_gate|sibling|汎用フック' docs/designs/codd-gate-design.md
```

- 総合終了コード: `0`（分解実行・`bash -c` 一括の両方で確認）
- part1 README 境界: hit（L275 `有効化は設定だけ` / L279 `パッケージ（agent_project）は codd_gate_* を import・結合・依存しない`）
- part2 README `regression_cmd` 等: hit（L276-277,280,282,285 ほか）
- part3 設計書の存在: `test -f` 成功
- part4 設計書の境界語: hit（`汎用フック` L248 / `sibling` L252,255,277 / `_apply_codd_gate` L361,366 …）

## 独立検算（現物突合）

- コード事実: `git grep -n '_apply_codd_gate' -- tools/agent-project` は `agent_project/configfile.py:201,376` のみ。
  設計書 §4.2 の主張（パッケージ内にしか現れない／sibling・tests には出ない）と一致。
- 正典受入 `! git grep _apply_codd_gate -- tools/agent-project` は現状 **FAIL**（ヒットあり→否定で非0）。
  §4.2 が「実装後に成立させる目標述語」と位置づける記述どおり（未実装のため想定内）。
- 否定 grep の論理（`! git grep` は無マッチ時のみ exit 0）は §4.2 の説明と正確に一致。
- 相互参照の実在: README の §4「プラグイン境界」(L239)・§4.1「任意部品」(L273)・§4.2「境界の完了条件」(L354)
  はすべて実見出し。§4.2 が指す §4.1「有効化／永続化」の太字ラベルも §4.1 本文に実在。デッドリンクなし。
- スコープ: 変更は `docs/designs/codd-gate-design.md` と `tools/agent-project/README.md` の2ファイルのみ
  （`git diff --name-only main HEAD` で `tools/agent-project/`・設計書以外の差分なし）。無関係差分の混入なし。

## @followup（スコープ外・minor）

- `tools/agent-project/README.md` L438 付近が「設計書 §4.1「外部 CLI の差し込み点」にカタログ化」と参照するが、
  §4.1 の実見出しは「値の組み立てと永続化を担う任意部品」。「外部 CLI の差し込み点」は §4 冒頭（L242）にある語。
  main 時点（§4.1=「自動検出レイヤ」）から既に不一致の**既存**ラベルずれで、本 run の diff は L438 を触っていない。
  完了条件 grep にも本タスクの変更領域にも掛からないため minor。参照先を §4 本文へ直すのが妥当（別タスク）。
