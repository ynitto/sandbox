# t9: coddgate.py に `codd_gate_debt_status()` と `codd_gate_summary_text()` を追加

**切り口**: 他候補が「t7-t9 予定分をまとめて一括実装」「トップレベルの既存 `codd_gate_debt.py`
（差分/負債の JSON パース）や `codd_gate_status.py`（no-op 縮退の 3 値モデル）をそのまま流用・
import」に流れる可能性がある中、本候補は **t9 の担当範囲（`codd_gate_debt_status` と
`codd_gate_summary_text` の2シンボルのみ）に厳密に絞り**、かつ `coddgate.py` 断片が単体
import 不可（モジュールレベル import 禁止）という制約下で成立する **純粋関数のみ**として
実装した。実装は `codd-gate.py`（本体 CLI）自身の `verify --debt` 判定ロジックと
`mr.py` の `finalize_task_mr` が組み立てる差し戻し理由（`why`）の文言規則を直接参照し、
それらと語彙・結合規則を一致させることを設計の軸にした。

## 成果

`tools/kiro-project/kiro_project/coddgate.py`（t5/t6 が追加した既存4シンボルは無変更）に
以下を追加。

```python
CODD_GATE_DEBT_LABELS = {
    "broken": "壊れた参照",
    "undocumented": "未文書化",
    "untested": "未テスト",
}


@dataclass(frozen=True)
class CoddGateDebtStatus:
    current: "dict[str, int]"
    baseline: "dict[str, int]"
    regressions: "list[str]" = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.regressions


def codd_gate_debt_status(
    current: "dict[str, int]", baseline: "dict[str, int]"
) -> CoddGateDebtStatus:
    ...  # current が baseline を上回った種別だけ regressions へ積む純粋関数


def codd_gate_summary_text(debt: "CoddGateDebtStatus | None" = None, *extra_reasons: str) -> str:
    ...  # debt.regressions + extra_reasons を "; " 結合し "codd-gate: " を前置。空なら空文字列
```

- `codd_gate_debt_status(current, baseline)`: `tools/codd-gate/codd-gate.py` の
  `verify --debt --json` が返す `{"broken": N, "undocumented": N, "untested": N}` と
  **同じキー**を current/baseline に使う純粋関数。種別ごとに `current > baseline` を判定し、
  上回った種別だけ `"{ラベル} {現在値} 件 > 基準 {基準値} 件"` 形式の文字列を `regressions` に
  積む（ラベルは同 CLI の `--debt` findings 文言「壊れた参照」「未文書化」「未テスト」と一致
  させた）。`baseline` に無い種別は判定しない——d1/d2（`.kiro-project/bus/runs/
  run-20260712-213419-5922/artifacts/d1,d2/`）が codd-gate 連携全体で一貫させる
  「不明・不足はすべて連携しない側に倒す」方針をラチェット判定にも踏襲した。例外は投げない。
- `CoddGateDebtStatus`: `ok` プロパティが `regressions` の有無で決まる frozen dataclass。
  `CoddGateNoopResult`（t6）・トップレベル `codd_gate_status.CoddGateStatus`（既存）と同じ
  「findings/regressions が1件でもあれば通さない」不変条件を踏襲した。
- `codd_gate_summary_text(debt=None, *extra_reasons)`: `debt.regressions` と
  `extra_reasons`（差分ゲート失敗理由など debt 以外の追加理由）を `"; "` で連結し
  `"codd-gate: "` を前置した1行の要約文字列を返す。連結規則は `mr.py` の
  `finalize_task_mr`（`why = "; ".join(problems)`）と一致させ、生成した文字列は同関数が
  組み立てる差し戻しコメント本文の `f"kiro-project: # 差し戻し（自動チェック）\n- {why}\n"`
  の `{why}` スロットへそのまま埋め込める形にした。理由が1つも無ければ空文字列を返す
  （呼び出し側は「差し戻し不要」と1行で判定できる）。

## 検証内容と結果

- `python3 -m py_compile tools/kiro-project/kiro_project/coddgate.py` → 構文OK。
- `grep -rq "codd_gate" tools/kiro-project/kiro_project/` → 終了コード0（マッチあり）。
- `python3 -m pytest tools/kiro-project/tests -q -k codd` → **63 passed**（t2/t3/t5/t6 と同値、
  無回帰）。
- `git status --short` → `M tools/kiro-project/kiro_project/coddgate.py` のみ。他ファイル無変更
  （`__pycache__` は生成されるが `.gitignore` 対象で `git status` に出ない）。
- 断片は単体 import 不可（`_head` 合成前提）のため、t6 と同じ手法で `shutil`/`dataclass`/
  `field` を注入した `sys.modules` 登録済みモジュールへ `exec` して実行時検証:
  - 悪化なし（`current<=baseline` 全種別）→ `ok is True`、`regressions == []`。
  - `broken` のみ悪化（12>10、他は基準内）→ `ok is False`、
    `regressions == ["壊れた参照 12 件 > 基準 10 件"]`。
  - `baseline` に無いキー（`untested`/`undocumented` 欠落）→ 判定対象外（`ok is True`）。
  - `CoddGateDebtStatus` が frozen（属性再代入で `FrozenInstanceError`）であることを確認。
  - `codd_gate_summary_text(debt)` 単体 →
    `"codd-gate: 壊れた参照 12 件 > 基準 10 件"`。
  - `codd_gate_summary_text(debt, "verify --strict NG")` →
    `"codd-gate: 壊れた参照 12 件 > 基準 10 件; verify --strict NG"`。
  - `codd_gate_summary_text(None)` / `codd_gate_summary_text(ok な debt)` → いずれも `""`。
  - `codd_gate_summary_text(None, "diff gate NG", "  ", "")` → 空白・空文字を除外して
    `"codd-gate: diff gate NG"`。
- `codd-gate verify --strict`（run 全体の完了条件の一部）は本タスク単体では未実行——
  `__init__.py`／`_FRAGMENTS` 登録・実結線は t1・t3・t5・t6 が既報告した前提崩れ
  （作業ブランチに main のパッケージ化 refactor が未マージで `kiro_project` が import 不能）
  に阻まれたまま、本タスクでも未解消。

## 採用した前提・未解決事項・範囲外で見つけた問題

1. **前提**: 対象ファイルは t5/t6 が積み上げた `tools/kiro-project/kiro_project/coddgate.py`
   （run 全体の完了条件のコマンドが参照するパス）と解釈した。
2. **前提（`codd_gate_debt_status` の入出力形）**: タスク文「現在値と基準値の比較で悪化を検出」
   を、`codd-gate verify --debt --json` が実際に返す `{"broken","undocumented","untested"}`
   の dict 形をそのまま current/baseline に使う設計とした。`--max-broken` 等の固定しきい値と
   前回スナップショットのどちらを baseline に渡すかは呼び出し側の自由（本関数は dict 比較しか
   しないので両方の使い方を吸収できる）。baseline を「今回どう調達するか」（前回 debt の
   ディスクへの永続化・`--max-*` 引数の組み立て）は結線タスクの範囲として持ち出さなかった。
3. **前提（`codd_gate_summary_text` の入出力形）**: タスク文「mr の差し戻し理由本文へ埋め込める
   要約文」を、`mr.py: finalize_task_mr` の `{why}` スロット（`"; ".join(problems)` 規則）に
   直接ハメられる文字列を返す関数、と解釈した。本文全体（`f"kiro-project: # 差し戻し...\n- {why}\n"`）
   の組み立てはこの関数の責務に含めず、`{why}` の中身だけを返す設計にした——本文テンプレート
   側（`mr.py`）を変更・複製しないため。
4. **未解決（t1/t3/t5/t6 既報告、本タスクでも再確認）**: 作業ブランチに main のパッケージ化
   refactor（`_head.py`/`__init__.py`/`_FRAGMENTS`）がまだマージされておらず、`kiro_project`
   は依然パッケージとして import 不能。`coddgate.py` を実際に `_FRAGMENTS` へ登録し
   regression/acceptance/enqueue の3フックへ結線する作業は、この前提崩れの解消（main merge の
   意思決定）を待つ別タスクの担当として引き継ぐ。
5. **未解決（新規）**: `codd_gate_debt_status` の baseline をどこから調達するか
   （例: `.kiro-project/` 配下に前回 debt スナップショットを保存する、charter に
   `--max-broken` 等の固定値を持たせる等）は本タスクでは決めていない。結線タスクが
   `codd_gate_hooks.py`（トップレベル既存）の `collect_debt_specs` 系と統合する際に、
   `CoddGateDebtStatus`/`codd_gate_summary_text` をどこから呼ぶか（enqueue フック内 or
   acceptance フック内）と併せて設計する必要がある。
6. **範囲外（未実施、t5/t6 と同じ理由で見送り）**: `__init__.py` 新規作成・`_FRAGMENTS` 登録・
   トップレベル `codd_gate_hooks.py`/`codd_gate_invoke.py` との統合・新規テストファイル追加。
   断片は単体 import 不可のため、テストは代わりに模擬名前空間での実行時検証（上記）で代替した。
7. **範囲外で新たに見つけた問題なし**: t1〜t3・t5・t6 が既報告した論点（main merge 未決定・
   `codd_gate_hooks.py`/`codd_gate_invoke.py` 棚卸し未割当・`codd_gate_status.py`/
   `codd_gate_debt.py` 専用テスト無し）以外に、本タスクの作業範囲内で新規の問題は見つからな
   かった。

data: {"delivery": {"url": "https://github.com/ynitto/sandbox/", "branch": "kp/kiro-project-codd-gate-171537", "commit": "05474668e9938a98b7ab6e6c52714cb56eaf12b2", "target": "main", "path": ""}}
