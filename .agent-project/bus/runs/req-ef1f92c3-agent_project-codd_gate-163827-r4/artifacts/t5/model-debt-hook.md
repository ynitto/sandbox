# model の debt 参照撤去と振る舞い等価の回復（r4 / t5）

**結論: 撤去そのものは r0 で済んでいた。r4 で残っていたのは「等価」の方で、そこに3つの破れがあった。全部塞いだ。**

対象は `tools/agent-project/agent_project/model.py` の1ファイルのみ。18 行追加・3 行削除。

---

## 1. 何が済んでいて、何が残っていたか

タスク指示は「`_codd_gate_debt_module` を除去または汎用名へ改名し、直接 import を排除する」だが、
これは r0 の `mdl` コミット（`2e5ff35`）で完了済みだった。現 HEAD の model.py に `codd_gate` は 0 ヒット。

r0 が行った置換:

| | 変更前 | 変更後 |
|---|---|---|
| 解決 | `_codd_gate_debt_module()` が sibling を遅延 import | 無し（本体同梱） |
| パース | `codd_gate_debt.parse_debt_output` → `DriftItem.to_spec()` | `_parse_intake_records`（本体内） |
| module 不在時 | 緩いパースへ no-op 縮退 | 分岐ごと消滅 |

差し込み点は `intake_cmd`（プロセス境界）に一本化された。t2 の設計どおり、debt は module フックを持たない
——`_hook_provider` を model から呼ぶ必要はなく、実際に呼んでいない。

残っていたのは**振る舞い等価**。r0 は `to_spec()` が担っていた `title` / `id` の型正規化を落とし、
生の dict を素通ししていた。

## 2. 塞いだ破れ 3 件

`intake_cmd` は本体の外にある任意のプロセスで、JSON の値が文字列である保証がない。
変更前後を同一ハーネスで実測した結果（`run_intake` の end-to-end）:

| 入力 | 変更前（r0 の素通し） | 変更後 | 変更前の元実装 |
|---|---|---|---|
| `{"id": 123}` | **AttributeError で watch ループが落ちる** | id `"123"` | id `"123"` |
| `{"id": 1.5}` | **AttributeError** | id `"1-5"` | id `"1-5"` |
| `{"id": 0}` | id が捨てられ自動採番・冪等キー喪失 | id `"0"` | id `"0"` |
| `{"id": "   "}` | id が文字列 `"task"` になる | 自動採番 | 自動採番 |
| `{"id": "  x  "}` | id `"x"`（偶然一致） | id `"x"` | id `"x"` |

1 件目は t2 が設計メモ §8 で予測していたもの。**2〜4 件目は t5 で新たに見つけた**（t2 の一覧には無い）。

`id: 0` の破れは `str(raw.get("id","") or "").strip()` という書き方をすると再発する——`0 or ""` は `""` に
潰れる。元実装は `raw_id not in (None, "")` で判定していたので `0` が生き残っていた。この一致を保つため
`or ""` を使わず元と同じ判定を書いた（コード中にコメントを残した）。

`run_intake` の except 節は `ValueError` のみ、呼び出し元（`loop.py:610` / `mr.py:558`）も無防備なので、
1 件目は例外が素通ししてループ全体を落とす。except を広げるのではなく正規化で根本を直した（t2 §3 の指示どおり）。

## 3. 等価の証明

sibling の `codd_gate_debt.py` は**未変更のまま残っている**ので、変更前の振る舞いの生き証人として使える。
23 ケースで `_parse_intake_records(text)` と `([i.to_spec() for i in parse_debt_output(text).items], errors)`
を直接突き合わせ、**23/23 完全一致**（specs・errors の両方）。

カバーした軸: 空/空白のみ/非 JSON、object 単体/配列、id が int・float・0・false・null・空文字・空白のみ・
前後空白・欠落、title が空・欠落・非文字列・空白のみ、非 object 混在、追加フィールド素通し、キー順、ネスト値。

## 4. 後続タスクが前提にしてよいこと

- **`_parse_intake_records` が返す spec の `title` / `id` は必ず `str` で strip 済み。** `id` キーは
  存在すれば非空文字列、正規化して空になったなら**キーごと不在**。
- title/id 以外のフィールドは解釈せず素通し（型も変えない）。
- `errors` の文言・分類は r0 から不変。intake+tests 担当が文言を assert してよい。
- model は `_hook_provider` を呼ばない。`agent_project/hooks.py` へ model からの依存は無い。
