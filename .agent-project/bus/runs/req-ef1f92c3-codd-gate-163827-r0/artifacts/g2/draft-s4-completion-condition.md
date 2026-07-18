**切り口**: 受入コマンドを「§4 の境界を測る決定的ゲート」として明文化し、実測に基づき exit 0 の逐語的意味と適用スコープ（判定面はパッケージ、任意 sibling と tests は対象外）まで確定させる草案にする（コマンドを貼るだけに留めない）。

---

## 設計書 §4 追記草案（`docs/designs/codd-gate-design.md`）

挿入位置: §4.1 の直後に新設小節 `### 4.2 境界の完了条件（決定的ゲート）` として置く。§4 冒頭 L241「本体は無改造」を測定可能にした受入であり、§4 の締めに来るのが自然。

> ### 4.2 境界の完了条件（決定的ゲート）
>
> §4 が定める境界、すなわち「agent_project パッケージは E1〜E6 の汎用フックだけを提供し、codd-gate を名指し・import・自動配線しない／codd_gate_* は任意の sibling 部品」を、散文の宣言ではなく機械可読な受入で固定する。設計上の完了条件は、次の 1 行が exit 0 を返すことである。
>
> ```
> ! git grep -nE '_apply_codd_gate|_codd_gate|import codd_gate' tools/agent-project
> ```
>
> `git grep` はマッチが 1 件でもあれば exit 0、皆無なら exit 1 を返す。先頭の `!`（agent-project の verify 記法で「非 0 を期待する」否定）がこれを反転させるので、対象にこの 3 パターンが 1 つも残っていないときにだけ全体が exit 0（PASS）になる。3 パターンはそれぞれ境界の別々の破り方を封じる。
>
> | パターン | exit 0 が保証すること |
> |---|---|
> | `_apply_codd_gate` | 起動時 Config 生成（`build_config`）へ codd-gate を自動配線する入口（`_apply_codd_gate_auto_wiring`）が無い。パッケージが codd-gate を能動的に組み込まない |
> | `_codd_gate` | パッケージ内に codd-gate を名指しする私設ブリッジ（`_codd_gate_wiring_module` / `_codd_gate_debt_module` / `doctor_codd_gate_findings` など）が無い |
> | `import codd_gate` | パッケージが codd_gate_* モジュールを直接 import しない（`import codd_gate_wiring` / `import codd_gate_debt`）。任意部品をパッケージへ引き込まない |
>
> 3 つが同時に空であることは、パッケージと codd-gate の結合が名前付きシンボルと import では 0 で、残る接点は E1〜E3 の汎用フックへ入れる値と `schemas/` の共通データ契約だけ、という状態を指す。§4 冒頭「本体は無改造」を測定可能にした言い換えである。散文の宣言は §4.1「現在地」の記述に上書きされうるが、この受入は上書きされない。毎回スキャンし直す決定的判定であり（no fake green・不変条件 1 と同じ規律）、境界が守られているかを人の読解ではなく exit code で決める。
>
> **適用スコープ**: この 3 パターンが対象にするのは、パッケージ（`tools/agent-project/agent_project/`）が codd-gate へ張る結合である。上の対象パス `tools/agent-project` には任意 sibling の `codd_gate_*.py` とその tests も含まれ、それらは `resolve_codd_gate` などの語を正当に持つ（`_codd_gate` に当たる）。境界が要求するのはパッケージ側の結合ゼロであって sibling 部品の不在ではないので、判定面はパッケージに絞る。すなわち対象パスは `tools/agent-project/agent_project` とし、任意 sibling と tests はこの受入で禁じる対象に含めない。
>
> この受入を PASS させるには、§4.1「現在地」が記述する `build_config` の自動配線と、doctor / model の `import codd_gate_*` ブリッジを除去する必要がある。除去そのものは本節の完了条件とは別で、ドキュメント整合（§4.1・README の書換）と実装側の後続タスクで扱う。

---

## (a) 成果 / サマリー

設計書 §4 に「境界の完了条件」を機械可読ゲートとして明記する草案を作成した（`artifacts/g2/draft-s4-completion-condition.md`）。中身は 3 点。

1. **完了条件の明文化**: 受入コマンド `! git grep -nE '_apply_codd_gate|_codd_gate|import codd_gate' tools/agent-project` が exit 0 を返すことを、§4 の境界（パッケージ＝汎用フックのみ／codd_gate_*＝任意 sibling）の設計上の完了条件として据える。挿入位置は §4.1 直後の新設 §4.2。
2. **exit 0 の逐語的意味**: `!` 否定と `git grep` の exit 規約を明示し、3 パターンが封じる境界違反を表で 1 対 1 に翻訳（`_apply_codd_gate`＝自動配線入口の不在／`_codd_gate`＝私設ブリッジの不在／`import codd_gate`＝任意部品の直接 import の不在）。
3. **適用スコープの確定**: 実測に基づき、対象パス `tools/agent-project` が任意 sibling の `codd_gate_*.py` と tests まで含む点を指摘し、境界が測るべき面はパッケージ `tools/agent-project/agent_project` だと確定させた。

## (b) 検証内容と結果

- **受入コマンドの実測**（作業ツリーで実行）: `git grep -nE '_apply_codd_gate|_codd_gate|import codd_gate' tools/agent-project` はマッチ多数で exit 0 → 否定込みの受入は現状 **exit 1（FAIL）**。ヒット内訳を確認した。
  - パッケージ（境界違反・除去対象）: `agent_project/configfile.py:201,220,376`（`_apply_codd_gate_auto_wiring` 定義と `build_config` からの呼び出し）、`agent_project/doctor.py:287,290,295,303,309,314,528`（`_codd_gate_wiring_module` と `import codd_gate_wiring`）、`agent_project/model.py:494,504,512,552`（`_codd_gate_debt_module` と `import codd_gate_debt`）。t1 の G1/G2 実測と一致。
  - 任意 sibling・tests（境界的に無問題）: `codd_gate_detect.py:39 def resolve_codd_gate(...)` ほか `codd_gate_status.py` / `codd_gate_wiring.py` / `tests/*` が `resolve_codd_gate` や `import codd_gate_debt` を正当に持ち、`_codd_gate` / `import codd_gate` に当たる。これらは任意部品であり除去対象ではない。
- **スコープ判定の裏取り**: パッケージ限定 `git grep ... tools/agent-project/agent_project` のヒットは上記 3 ファイルのみ。判定面をパッケージに絞れば、受入 exit 0 が「パッケージ側の結合ゼロ」＝目標境界を過不足なく表すことを確認した。
- **既存記述との整合**: §4 冒頭 L241–244（本体無改造・E1/E2/E3 使用）と §1 不変条件 1（毎回スキャン・no fake green）に接続する形で草案を書いた。§4.1「現在地」L284–304 の自動配線記述とは相反するため、草案末尾で「PASS には §4.1 の自動配線とブリッジ除去が前提」と依存関係を明示（除去自体は別タスク）。
- 調査・草案作成のみ。設計書はリポジトリルート `docs/designs/` にあり編集許可範囲（`tools/agent-project` 配下）外なので直接編集していない。作業ツリーのファイルは変更なし（git 規約・スコープ遵守）。テスト/型チェックはドキュメント草案のため対象外。

## (c) 採用した前提・未解決・範囲外

- **採用した前提**:
  - 完了条件の対象は「境界そのもの」＝パッケージが codd-gate へ張る名前付き結合。exit 0 の意味を「パッケージ結合ゼロ」と固定した（t1 の境界解釈と受入 `_apply_codd_gate` 消滅要求を根拠）。
  - `!` は agent-project verify の「非 0 期待」否定と解釈（受入がこの前提で書かれているため）。
  - 挿入位置は §4.1 直後の新設 §4.2。§4 の締めとして境界宣言（§4 冒頭）→ 部品説明（§4.1）→ 完了条件（§4.2）の順で読ませる。
- **未解決事項（評価役・後続の判断待ち）**:
  - **対象パスの確定**: run の受入はパスを `tools/agent-project` にピン留めしているが、実測どおりこのパスは任意 sibling の `codd_gate_*.py` と tests まで含み、それらが `_codd_gate` 等に当たるため、sibling を `tools/agent-project` 直下に残す限り exit 0 に到達できない。草案では「判定面をパッケージ `tools/agent-project/agent_project` に絞る」を既定として推奨した。**受入コマンドのパスを `…/agent_project` に修正するか、`tools/agent-project` のまま sibling を別ディレクトリへ移すか**は、境界の置き場所を決める判断なので評価役に委ねる（前者を推奨）。
  - t1 の未解決（doctor/model の sibling import を境界としてどう扱うか）と連動する。パッケージ結合ゼロを厳格に取るなら doctor/model のブリッジも除去対象で、この受入が自動的にそれを要求する。
- **範囲外で気づいた点（手を出さず記載）**:
  - `@followup` 受入コマンドのパスを `tools/agent-project/agent_project` へ修正する（run 定義・charter acceptance 側）。現行 `tools/agent-project` のままだと任意 sibling を巻き込み、境界を過剰に禁じてしまう。
  - `@followup` 設計書 `docs/designs/codd-gate-design.md`（リポジトリルート・本タスクの編集許可外）への草案反映と、§4.1「現在地」L284–304・README L279–286 の書換（t1 G1/G2）は別タスク。
  - `@followup` 完了条件を PASS させる実装（`_apply_codd_gate_auto_wiring` と doctor/model の `import codd_gate_*` ブリッジ除去）は本 run のスコープ外（実装変更は「やらないこと」）。
