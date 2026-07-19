# codd_gate_wiring / codd_gate_base の切り離しと責務限定（t2）

対象ファイル: `tools/agent-project/codd_gate_wiring.py`・`tools/agent-project/codd_gate_base.py`
（付随して `tests/test_codd_gate_wiring.py` 追記・`tests/test_codd_gate_base.py` 新規）
検証コマンド: `PYTHONPATH=tools/agent-project python3 -m unittest discover -s tools/agent-project/tests -p 'test_codd_gate_*.py'`

## 0. 着手時点の状態（重要・そのまま報告する）

割り当てられた worktree には、**本タスクの過去の試行によるコミット前の変更が既に残っていた**
（`codd_gate_base.py` / `codd_gate_wiring.py` / `tests/test_codd_gate_wiring.py` の変更と
`tests/test_codd_gate_base.py` の新規）。artifact ディレクトリ `t2/` は空で、報告は残っていない。

そこで本タスクは、**残っていた変更を完了条件と1項目ずつ突き合わせて検証し、不足があれば補う**
方針を採った。検証の結果、production 側（2モジュール）は完了条件を満たしており、追加の
コード変更は行っていない。以下は「何がなされているか」を独立に確認した結果である。

## 1. 切り離しの現在地 — 2モジュールから agent_project への経路

| 観点 | 確認方法 | 結果 |
|---|---|---|
| パッケージの import | `grep -n "agent_project\|import agent"` | ヒット2件。いずれも**docstring の散文**（`codd_gate_base.py:14`「import せず」・`codd_gate_wiring.py:16`「import しない」）。実 import は **0** |
| cfg / yaml への書き込み | `grep -nE "write_text\|open\(.*[wa]\|\.write\(\|upsert\|apply_to_file"` | ヒット2件。いずれも**書き込みは別モジュールが担う**と書いた docstring（`:19`・`:214`）。実書き込みは **0** |
| sibling → パッケージの静的依存 | 上記2件 | **0 本** |
| パッケージ → sibling の固有名 | t1 の調査（`grep -rni "codd" agent_project/`）を踏襲 | **0 件**。結線は `hooks.py` の能力レジストリ経由のみ |

**「切り離し」は削除ではなく、依存の向きが既に逆転済みであることの追認＋その事実の明文化**として
成立している。実行時に本体へ触れる経路は 2 モジュール側に一つも無く、本体が能力
（`wiring.detect` / `wiring.findings`）で sibling を引き当てる一方向だけが残る。

## 2. 責務の限定 — 完了条件の4項目との対応

| 完了条件の責務 | 担当 | 実体 |
|---|---|---|
| sibling 検出 | `codd_gate_wiring.detect_wiring()` | `codd_gate_detect` の各実測関数を呼ぶ**唯一の配線**。実在→版→schema 互換→能力の短絡順 |
| 推奨文字列の生成 | `recommend_regression_cmd()` / `recommend_intake_cmd()`（判定は `judge_wiring()`） | 純粋関数。usable かつ repos_path 既知かつ当該サブコマンドが使えるときだけ返す |
| yaml への冪等注入 | **`codd_gate_regression.py`（本タスクの対象外）** | 2モジュールは一切書かない。`codd_gate_wiring.py:18-20` が委譲先として明示 |
| （必要なら）CLI 所見の出力 | `codd_gate_wiring.main()` | 読み取り専用。所見を JSON で stdout へ。設定は書き換えない |

`codd_gate_base.resolve_base_rev()` は上記4項目のいずれにも属さない。判断は §3 に記す。

docstring からは、境界の外を指していた記述を落としてある: 分割前の `agent-project.py:4906-` /
`:831` / `:5514-5519` / `:3477` への行番号参照（t1 の申し送り④。現在は全滅している）と、
run 内部のタスク ID（`a1` / `a2` / `a4` / `b2` / `b3` / `d2` と artifacts パス）。
`grep -nE "agent-project\.py:[0-9]|run-2026|artifacts/d[0-9]"` のヒットは **0**。

## 3. 宙に浮く公開関数の判断（削除せず、明示呼び出し用 API として残す）

### 3.1 棚卸し

本番経路（doctor → hooks → `detect_wiring`/`doctor_findings`）から到達できるかで分類した。

| 公開シンボル | 到達性 | 判断 |
|---|---|---|
| `codd_gate_wiring.detect_wiring` / `doctor_findings` | hooks 能力の契約そのもの＋CLI | 現役 |
| `judge_wiring` / `WiringJudgment` | `detect_wiring` が呼ぶ（:143） | 現役 |
| `regression_wired` / `intake_wired` / `recommend_*_cmd` | `judge_wiring` が呼ぶ（:134-142） | 現役 |
| `read_configured_cmd` / `main` | CLI 経路 | 現役（本タスクで追加された CLI 所見の出口） |
| **`codd_gate_base.resolve_base_rev` / `FALLBACK_BASE_REV`** | **本番経路からの呼び出し元ゼロ** | **存置**（下記） |

`grep -rn "resolve_base_rev\|codd_gate_base"` の結果、production コードからの呼び出しは 0 件。
残るのは自ファイル・新規テスト・`codd_gate_routing.py:11` の設計判断への言及・
`docs/designs/codd-gate-design.md:270` の責務表のみ。

### 3.2 `resolve_base_rev()` を残す理由

1. **埋めている穴は消えていない。** codd-gate の差分モードは `--base` と `$KIRO_BASE_REV` の
   どちらも空だと終了する。一方 `recommend_regression_cmd()` は `--base "$KIRO_BASE_REV"` を
   **シェル変数参照のまま**埋め込む設計で、Python 側では誰も具体値に解決しない。非 git
   ワークスペース・初回コミット前など注入元が基準 rev を持てない環境では、シェルで空文字に
   展開されて同じ終了に落ちる。この穴を塞ぐ優先順位（env → base ブランチ → `HEAD~1`）は、
   自動配線を切り離しても依然として必要な知識である。
2. **削除すると知識が復元不能になる。** 中身は環境変数とフォールバックの優先順位という
   **規約の表明**であって、実装は 6 行。消せば次に必要になった呼び出し元が同じ優先順位を
   再発明することになり、しかも再発明した実装が `HEAD~1` フォールバックまで一致する保証がない。
3. **存置のコストがほぼゼロ。** 標準ライブラリ（`os`）のみに依存する純粋関数で、I/O も
   subprocess も起動しない。誰も import しなければ実行時コストは 0。zipapp への同梱は
   `install.sh:50` の `codd_gate_*.py` glob なのでファイル追加・削除の手間も無い。
4. **「自動では配線されない」ことは削除でなく明示で担保する。** docstring の
   「明示的に import して呼ぶ API で、自動では誰にも配線されない」がこれを宣言し、
   `codd_gate_wiring.py:78-80` が「具体値まで解決したい呼び出し元は `codd_gate_base.resolve_base_rev()`
   を使う」と発見可能性を与える。**未配線であること自体が意図された状態**だと読める。
5. **契約はテストで固定した。** 呼び出し元が居ない公開 API は仕様が腐りやすいため、
   `tests/test_codd_gate_base.py`（新規・8 ケース）で優先順位・空文字の落ち方・
   「戻り値が決して空にならない」（`--base ""` を組み立てさせない本丸）を固定した。
   `TestModuleBoundary` でパッケージ非依存も併せて固定している。

`codd_gate_debt.py` も本番経路からの呼び出し元がゼロだが、docstring 自身が「呼び出し側のための
独立したアダプタとして残る」と宣言しており、判断は既に済んでいると読める（かつ本タスクの
対象ファイル外なので触っていない）。

## 4. CLI 所見の出口（`python3 codd_gate_wiring.py`）

完了条件の「（必要なら）」を「**必要**」と解釈した。理由は、切り離し後に
「いま結線されているか／未結線なら何を書けばよいか」を確かめる手段が doctor（＝ホスト稼働が前提）
しか無くなるため。ホストが居なくても module 単体で完結する読み取り専用の入口を1つ持たせた。

- 読むだけ。`--config` の yaml も cfg も書き換えない（書き込みは `codd_gate_regression.py` の CLI）。
- yaml パーサは `codd_gate_regression.upsert_config_text` と同じ**行ベース・同じ indent 許容**
  （`^[ \t]*<key>:`）で揃えた。PyYAML の load→dump は人手のコメントを落とすため使わない。
  実装が食い違うと「読み取りは結線済みと言うのに書き込みは別の行を触る」が起きるので、
  両者のアンカーが一致していることを確認済み（`codd_gate_regression.py:96` の `_key_pattern` と同一）。
- `codd_gate_regression` の import は `main()` 内の**遅延 import**。フック経路
  （`detect_wiring`/`doctor_findings`）に yaml 書き込み側の module を持ち込まないため。

実行例（この検証環境には codd-gate が実在するため `usable: true` になっている）:

```
$ python3 codd_gate_wiring.py --config /tmp/does-not-exist.yaml
{"usable": true, "reason": "", "regression_wired": false, "intake_wired": false,
 "findings": [{... "fix": "agent-project.yaml に設定: regression_cmd: 'codd-gate verify --base \"$KIRO_BASE_REV\" --repos .agent-project/repos.json'"}, ...],
 "config": "/tmp/does-not-exist.yaml", "repos": ".agent-project/repos.json"}

$ python3 codd_gate_wiring.py --config /tmp/t2-cfg.yaml   # regression_cmd 結線済みの yaml
{"usable": true, ..., "regression_wired": true, "intake_wired": false, "findings": [<intake の1件のみ>], ...}
```

## 5. 検証

| 検証 | 結果 |
|---|---|
| `unittest discover -p 'test_codd_gate_*.py'` | **97 tests OK**（0.699s） |
| CLI 実行（設定ファイル不在） | rc=0・所見2件・クラッシュせず未結線へ縮退 |
| CLI 実行（regression 結線済み yaml） | rc=0・`regression_wired: true`・所見は intake の1件のみ・**ファイル内容は不変** |
| hooks 解決が壊れていないこと | `_hook_provider("wiring.detect") -> codd_gate_wiring`／`("wiring.findings") -> codd_gate_wiring` |
| パッケージ import・書き込みの不在 | §1 の grep どおり実ヒット 0 |
| 分割前の行番号参照の不在 | `grep -nE "agent-project\.py:[0-9]"` → 0 |
| 全体テスト（`test_*.py` 全件） | 実行時間が長く、本 artifact 執筆時点で継続中。結果は作業報告に記載 |

CLI の「書き換えないこと」は目視ではなくテストで固定した
（`TestCli.test_reports_wired_state_without_touching_config` が実行前後のファイル内容一致を主張）。

## 6. 採用した前提

- **「切り離し」を「除去の追認＋明文化」と解釈した。** t1 が確認したとおり
  `_apply_codd_gate_auto_wiring` は既に実装から消えている。よって本タスクの実質は
  「2モジュール側に本体への逆流が残っていないことの検証」と「docstring が新境界を語ること」であり、
  存在しないコードを消す作業ではない。
- **「（必要なら）CLI 所見の出力」を必要と判断した**（理由は §4）。
- **宙に浮く公開関数＝`resolve_base_rev` のみと判定した。** 判定軸は「本番経路（doctor → hooks）
  からの到達性」。CLI とテストからの到達は「明示呼び出し」として到達性に数えていない
  （数えると新設の CLI が自分で自分を正当化してしまうため）。

## 7. 範囲外で見つけた問題（手を出していない）

- @followup **`tests/test_codd_gate_wiring.py` が t2 と t6 の両方で末尾に追記されている。**
  本 worktree には t6 の `TestHookResolution` が存在せず、t6 の artifact には本タスクが追加した
  `TestReadConfiguredCmd` / `TestCli` が載っていない。同一ファイルの末尾へ双方が append する形の
  ため、ブランチ統合時に**コンフリクトが出る可能性が高い**（内容は排他で、どちらも残すのが正）。
- @followup `codd_gate_routing.py:11` の docstring が、分割前の `agent-project.py 側の型` という
  言い回しのまま残っている（行番号は無いので実害は小さい）。対象ファイル外のため触っていない。
- @followup `docs/designs/codd-gate-design.md:270` の責務表は `resolve_base_rev()` を「穴を埋める」と
  書くが、**誰も呼んでいない（＝明示呼び出し用 API である）ことに触れていない**。§3.2 の判断を
  設計書へ反映するなら 1 行の追記が要る。設計書は本タスクの対象外。
- @followup `read_configured_cmd` は行末のインラインコメント（`regression_cmd: pytest -q  # 注釈`）を
  値の一部として返す。`codd_gate_regression` の書き込み側も同じ粒度なので不整合ではないが、
  厳密な値が要る呼び出し元が現れたら再検討が要る。
