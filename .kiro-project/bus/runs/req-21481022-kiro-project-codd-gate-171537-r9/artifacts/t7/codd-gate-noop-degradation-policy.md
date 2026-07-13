# codd-gate 未インストール時の no-op 縮退方針

対象: `tools/kiro-project/kiro-project.py` 本体へ codd-gate 連携を結線する全タスク
（regression/acceptance/enqueue の3フック — b1-b3/c1-c2/e1-e2 等）が共通で遵守する方針。
`codd_gate_detect.py`（[t1](../t1/codd_gate_detect_api_contract.md)）・`codd_gate_invoke.py`
（[t2](../t2/codd-gate-invoke-caller-contract.md)）の契約と、既存実装
（`codd_gate_status.py`）が体現している設計を、結線タスク向けに一般化・明文化したもの。
本タスクは文章化のみでコード変更は行っていない。

## 前提

codd-gate は **任意依存（optional）** の外部ツールである。導入済み環境では一貫性ゲートとして
機能するが、未インストール・バージョン非対応・schema 非互換のいずれの環境でも、
kiro-project 本体の既存の合否判定・振る舞いには一切影響を与えてはならない。この「影響を与えない」
状態を本方針では **no-op 縮退** と呼ぶ。

## 三原則

### 1. 検出は `codd_gate_detect` の戻り値のみに依存する

- 結線コードが「codd-gate が使えるか」を判断する唯一の入力は、`codd_gate_detect.py` の公開関数
  （`resolve_codd_gate` / `resolve_codd_gate_bin` / `get_version` / `check_repos_schema_compat` /
  `detect_capabilities`）の**戻り値**、またはそれらを合成した `codd_gate_status.py` の
  `CoddGateStatus.usable` / `.command()` / `.reason` である。
- 独自に `shutil.which` を再実行する、`--version` を自前でパースする、`.kiro-project/` 配下の
  ファイル存在で代替判定する、といった検出ロジックの重複実装・迂回を行わない。判断ロジックを
  一箇所（`codd_gate_status.build_status`）に閉じ込めることで、フォールバック条件が3フック間で
  食い違うリスクを排除する。
- 呼び出し側が書くのは事実上 `if status.command(...):` の1行分岐のみでよい
  （`codd_gate_status.py` 冒頭コメントに明記された設計）。`usable` の真偽を導出するための
  追加条件分岐（バージョン比較・schema 検証の再実装等）を結線コード側に持ち込まない。

### 2. 例外を外へ伝播させない

- codd-gate 関連の呼び出し（検出・バージョン確認・schema 検証・プロセス起動）に起因する例外は、
  結線コードから見て**発生しないものとして扱える**状態でなければならない。ただし内部の防御範囲は
  モジュールごとに非対称であるため（t1 の所見）、結線コード側の防御責務は次の通り階層化する。
  - `invoke_codd_gate`（t2）: `OSError` / `subprocess.SubprocessError`（`TimeoutExpired` 含む）を
    関数内部で捕捉し `CoddGateResult(status="skipped")` へ縮退させる。**呼び出し側は
    `invoke_codd_gate` の呼び出しを try/except で囲む必要はない**。
  - `detect_status`（`codd_gate_status.py`）: `resolve_codd_gate` / `get_version` の呼び出しを
    それぞれ自前の try/except で包み、想定外の例外（`resolve_codd_gate` は無条件伝播、
    `get_version` は `OSError`/`SubprocessError` 以外を伝播しうる）を「未検出」
    （`binary=None`）または「バージョン不明」（`version_known=False`）へ縮退させている。
    **結線コードが `detect_status` を使う限り、この階層で既に吸収済み**であり、
    追加の try/except は不要。
  - `detect_status` を経由せず `build_status` を直接呼ぶ、または `resolve_codd_gate_bin` 等の
    低レベル関数を結線コードから直接呼ぶ場合は、上記の防御を自前で用意する責務が発生する
    （t1 が指摘する非対称性を踏まえ、`resolve_codd_gate` / `get_version` /
    `check_repos_schema_compat` / `detect_capabilities` の呼び出し箇所には
    `except Exception:` 相当の防御を必ず添える）。
- 原則として結線コードは `detect_status` → `CoddGateStatus.command()` → `invoke_codd_gate` の
  合成パスのみを使う。このパス上では低レベル関数を直接呼ばないため、上記の非対称性を意識する
  必要はない。

### 3. 既存の合否・理由・enqueue 結果を一切変えない

- `invoke_codd_gate` の戻り値 `CoddGateResult.status` が `"skipped"` の場合
  （未検出・非互換・起動失敗・タイムアウトのいずれか）、結線コードは
  **codd-gate 連携が存在しなかった場合と完全に同じ経路**へフォールバックする。
  regression の合否判定・acceptance の受理判定・enqueue（負債取り込み）の結果を、
  `"skipped"` を理由に変更してはならない（＝既存の kiro-project 本体ロジックがそのまま決定する）。
- `"skipped"` は「わからない・使えない」であって「NG」ではない
  （t2 5節）。`"failed"`（codd-gate が実際に起動しプロセスとして非0終了した）とは明確に区別し、
  `"failed"` の場合にのみ codd-gate 自身のゲート判定を合否へ反映する。`"skipped"` を
  `"failed"` 相当として扱う実装は本方針違反。
- `status.reason` / `result.reason` は診断・ログ（journal 等）にのみ使用し、合否判定の分岐条件
  として使わない。ログへの記録は許可されるが、それによって既存の journal フォーマットや
  finding 構造を破壊的に変更しない（追記であり置換ではない）。
- codd-gate が **利用可能**（`usable=True`）で実際に呼び出され `"ok"`/`"failed"` が返った場合の
  合否反映方法は本方針の対象外（各フックの個別設計に委ねる）。本方針が固定するのは
  「未インストール・非互換・起動失敗・タイムアウト＝ `"skipped"`」の経路が既存挙動を
  変えないことのみ。

## 適用パターン（regression/acceptance/enqueue 共通）

```python
status = detect_status(explicit=cfg.codd_gate_bin)          # a1/a4 — 例外はここで吸収済み
argv = status.command("verify", *build_routing_args(...), "--base", base_rev, "--strict")
if argv is None:
    # usable=False（未検出・非互換）。codd-gate を一切起動せず、
    # 既存の regression/acceptance/enqueue ロジックをそのまま実行する。
    ...  # 既存の合否判定はここで完結。codd-gate 関連の分岐は一切増やさない
else:
    result = invoke_codd_gate(status, "verify", ...)         # try/except 不要（t2 4節）
    if result.status == "skipped":
        ...  # ここも既存ロジックへフォールバック。argv is None のケースと同じ扱い
    elif result.status == "failed":
        ...  # 本物のゲート失敗として合否に反映する（フック個別の責務）
    else:  # "ok"
        ...
```

`status.command()` が `None` を返す分岐と、`invoke_codd_gate` が `"skipped"` を返す分岐は、
**結線コードから見て同一の「no-op」終端**として扱う。両者を異なる経路として実装しない
（前者はプロセス起動前の縮退、後者は起動後の縮退という違いはあるが、結果として既存挙動へ
フォールバックする点は同じ）。

## アンチパターン（禁止事項）

- 結線コードで `shutil.which("codd-gate")` 等を独自に呼び、`codd_gate_detect`/`codd_gate_status`
  を経由しない検出を行う。
- `detect_status`/`invoke_codd_gate` の呼び出し箇所に、契約上不要な追加の try/except を
  「念のため」で重ねる（t2 が「try/except 不要」と明言している設計を無視した防御的コーディングは、
  例外処理の重複による可読性低下を招くため避ける。ただし t1 の非対称性に該当する低レベル関数を
  直接呼ぶ場合は例外）。
- `"skipped"` を理由に regression/acceptance を失敗扱いにする、または enqueue をスキップする
  （codd-gate 未インストール環境で既存タスクが通らなくなる＝本方針の趣旨に反する）。
- `status.usable` や `result.status` の判定を、`CoddGateStatus`/`CoddGateResult` が公開する
  プロパティ以外の手段（`findings` の中身を直接パースする等）で代替する。

## 検証内容と結果

- 依存成果物 [t1](../t1/codd_gate_detect_api_contract.md)・[t2](../t2/codd-gate-invoke-caller-contract.md)
  を読了し、記載されている戻り値型・例外捕捉範囲・`"skipped"` 判定条件と、本方針の記述に
  齟齬がないことを確認した。
- 既存実装 `tools/kiro-project/codd_gate_status.py`（`CoddGateStatus`/`build_status`/`detect_status`）
  ・`codd_gate_base.py`・`codd_gate_routing.py` を全文読了し、これらのモジュールが既に
  「例外を外に漏らさない」「usable=False は command()=None の1行分岐に集約する」という
  本方針と同じ設計思想で実装済みであることを確認した（本方針はこの既存実装を後続の結線タスク
  向けに一般化・明文化したものであり、実装と矛盾しない）。
- 本タスクは方針の文章化のみであり、コード・テストの変更は行っていない。
  `python3 -m pytest tools/kiro-project/tests -q -k codd` 等のコマンド実行によるテストは、
  結線タスク（b1-b3/c1-c2/e1-e2）が実装を追加した後に意味を持つものであり、本タスク単体では
  対象外と判断した。

## 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: タスク文の「共通方針を決める」「文章化する」を、regression/acceptance/enqueue の
  3フックを実装する後続タスクが参照する規約文書を作成することと解釈した。コード変更は求められて
  いないと判断し、作業ツリーへの変更は行っていない。
- **前提**: 「既存の合否/理由/enqueue 結果を一切変えない」は、`"skipped"` 発生時に限った制約
  （codd-gate が実際に稼働し `"failed"` を返した場合の合否反映方法までは制約しない）と解釈した。
  理由: t2 の設計が `"skipped"`/`"failed"` を明確に区別しており、`"failed"` は「本物のゲート失敗」
  として合否に反映することを前提にしている（t2 2節）。この解釈を本文書中に明記した。
- **範囲外で見つけた問題**: t1 が指摘した「`resolve_codd_gate`/`get_version`/
  `check_repos_schema_compat`/`detect_capabilities` の例外捕捉範囲の非対称性」は、
  `detect_status` を経由する限り結線タスクには影響しない（`detect_status` 側で吸収済み）。
  ただし将来 `check_repos_schema_compat`（schema 適合判定）を結線タスクが直接呼ぶ設計になった場合
  （`detect_status` は schema 判定を含まないため）、この関数呼び出しには結線コード側で
  `except Exception:` 相当の防御が別途必要になる点を本方針の「適用パターン」節・「原則2」節に
  明記した。本タスクでの実装はしない。
- 未解決事項なし。
