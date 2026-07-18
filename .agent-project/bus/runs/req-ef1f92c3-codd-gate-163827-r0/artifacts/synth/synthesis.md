# synth — codd-gate 連携の目標境界を設計書に固定（3草案統合）

検証済み3草案（g1 境界規範・g2 完了条件・g3 §4/§4.1 責務分離）を1つの境界文書へ統合し、
作業ツリーの2ファイルへ書き込んだ。gate（verify=fail）の指摘は統合時にすべて解消し、
本反復で slop-police の残り1件（§4.1 見出しの装飾ダッシュ）を直して仕上げた。

## 書き込んだ成果（working tree）

- `docs/designs/codd-gate-design.md`
  - §4 冒頭に **プラグイン境界（規範）3条**（L246–259）。パッケージ（`agent_project/*`）＝汎用フックのみ・
    codd-gate を名指し／import／自動配線しない／`codd_gate_*.py`＝`tools/agent-project/` 直下の任意 sibling／
    `regression_cmd`・`intake_cmd` の書き手は一つ（有効化=yaml・CLI／永続化=`codd_gate_regression.py`）。
    起動時 `build_config` が値を差す経路は無し。
  - §4.1 見出しを「自動検出レイヤ」→「値の組み立てと永続化を担う任意部品（`tools/agent-project/codd_gate_*.py`）」へ
    （L273）。リード・モジュール表（`codd_gate_debt`／`codd_gate_wiring`／`codd_gate_regression` 行）を実コード照合済み
    API に整え、`codd_gate_wiring` を「doctor 表示・生成の材料。パッケージへは配線しない」に再フレーム。
  - §4.1「現在地（結線状況）」段落（build_config 自動配線）を削除し、**有効化／永続化**の2段落（L301–314）へ全面書換。
    yaml 人専有段落を再フレームし（L316–320）、**任意部品の可搬性（欠落時の挙動）**段落を新設（L322–327）。
  - §4.2 **境界の完了条件（決定的ゲート）**を新設（L354–386）。正典受入 `! git grep _apply_codd_gate -- tools/agent-project`
    （exit 0）を明記。広い `_codd_gate|import codd_gate` はパッケージ（`agent_project/`）限定の補助確認として別掲。
  - front-matter L4 の関連表記を「任意 sibling 部品含む」に整合（波及修正）。
- `tools/agent-project/README.md`
  - 一貫性ゲート節（L272–289）を同じ境界へ整合。必須文言を全て含む: 非依存（`agent_project` は codd_gate_* を
    import・結合・依存しない, L279）／パッケージは持たず sibling（`codd_gate_*.py` は直下の任意 sibling, L285）／
    有効化は設定（**有効化は設定だけ**, L275）／`regression_cmd`・`intake_cmd`・`codd_gate_*.py`（L276–281,285）／
    自動検出（build_config が codd-gate を自動検出して値を差し込む配線層は無い, L282）。リンク先を §4「プラグイン境界」・
    §4.1「任意部品」・§4.2「境界の完了条件」へ更新。

## 検証（現物突合）

- 設計 §4.2 の事実主張をコードで実測確認:
  - `_apply_codd_gate` は `agent_project/configfile.py:201,376` のみ。sibling `codd_gate_*.py`・tests には出ない（主張どおり）。
  - 正典受入 `! git grep _apply_codd_gate -- tools/agent-project` は現状 **FAIL**（`configfile.py` にヒット）。コード未変更
    のため想定どおりで、§4.2 が指す「実装後に成立させる目標述語」と一致。
  - 広い `_codd_gate|import codd_gate` をパッケージ限定で見ると configfile/doctor/model にヒット、パスを
    `tools/agent-project` へ広げると sibling 4本＋tests 6本を巻き込む（§4.2 の「広いパターンはパッケージ限定で使う」記述どおり）。
- 死んだ相互参照なし。§4.1 見出し変更後も README「§4.1「任意部品」」・§4.2→§4.1「有効化／永続化」は実在ラベルを指す。
- 両ファイルの境界文言は同一（下記 constraints）。README 必須文言5点を充足。

## slop-police（本反復の仕上げ）

- §4.1 見出しの装飾ダッシュ「— 値の組み立てと永続化」を house style の名詞句
  「値の組み立てと永続化を担う任意部品（…）」へ（規則14: 全角ダッシュ回避／命題型見出し回避）。「任意部品」ラベルは保持。
- 残る `——`／偏愛語「まさに」は §4「差し込み点選択の妥当性」の**据え置きブロック**（g3 が変更対象外と明示・diff の
  文脈行）にあり、統合スコープ外。編集すると既存散文への scope creep になるため触れず、@followup に記載。
- 中黒列挙「名指し・import・自動配線しない」は両ドキュメント共通の境界必須文言（正確さを担う技術用語列）のため温存。
  コロンは house style の半角（design 19件・README 大勢が半角）に一致。

## gate 指摘の解消（前反復・差し戻し3件＋minor、統合済み）

- **[fail①]** g1 P3 の `\s` false green（`git grep -E` は `\s` をリテラル `s` 扱い）→ §4.2 はアンカー無しの素の
  `import codd_gate`（＋`_codd_gate`）を採用。
- **[fail②]** 完了条件の3草案不一致 → 正典を単一 `_apply_codd_gate`（パス `tools/agent-project`）に固定。
  `_apply_codd_gate` はパッケージ限定のため **g2 の「受入パスを `…/agent_project` へ」@followup は撤回**。
- **[fail③]** g2→g3 デッドリンク → §4.2 の参照先を g3 の新段落「有効化／永続化」へ揃えた。
- **(minor)** 連結正規表現の冗長性（`_apply_codd_gate ⊂ _codd_gate`）を §4.2 に注記／dashboard 用語のぶれ →
  doctor 表示（`doctor_findings`）に統一／`codd.?gate` 総当たり（P4）はコメント・docstring 巻き込みのため不採用。

## 統合上の判断（矛盾・重複・欠落の扱い）

- **重複の統合**: g1「4.0 規範」と g3「境界3条」は同一境界の別表現。g3 の単一書き手フレーミングを骨格に、g1 の
  禁止事項（名指し・import・自動配線しない）を条文へ吸収して1ブロックに集約。
- **欠落の充足**: t1 の G1/G3/G4/G5/G6 を本文で閉じた。G4 の §1 不変条件への1条追記は synth goal（§4/§4.1＋README）外の
  ため @followup とし、§4 規範ブロックで境界を規範化。
- **矛盾の解消**: 「未検出→no-op（自動配線が有効な限り）」という旧記述を、境界と整合する「生成ツールが
  `usable=False` を見て値を書かない」へ統一。

## 境界スコープの判断（要確認事項として記録）

ワークスペース制約は「tools/agent-project 配下のみ」だが、`docs/designs/codd-gate-design.md` はリポジトリルート
（配下外）にある。次の根拠から設計書は本タスクの意図された書込対象と判断して編集した: charter 成果物に「設計書」が
明記、synth goal が設計書 §4/§4.1 への書き込みを明示、run の目的が「設計書に固定」、下流 loop が設計書を grep 検証、
run 内に設計書を書く他ノードが存在しない。「他フォルダに触らない」は姉妹ツール subtree（agent-flow 等）・skills を
指すと解し、それらには一切触れていない。

## @followup（本 run スコープ外・実装/他節）

- @followup 実装: 受入 `! git grep _apply_codd_gate` を PASS させるには `configfile.py` の
  `_apply_codd_gate_auto_wiring` を除去する（コード変更＝「やらないこと」スコープ外）。
- @followup 実装（境界の完全形）: `doctor.py`（`import codd_gate_wiring`）・`model.py`（`import codd_gate_debt`）の
  sibling import を除去すると §4.2 の広い述語も PASS する。除去 or 汎用 optional-import として温存かは境界の厳密度の
  意思決定（未解決・人の判断待ち）。
- @followup 文書: §1「不変条件」に第7条（パッケージは codd-gate を名指し・import・自動配線しない／`codd_gate_*` 欠落でも
  同一挙動）を追記し、境界の規範化を §4 散文だけに留めない。
- @followup slop: §4「差し込み点選択の妥当性」据え置きブロックの `——`（2箇所）・「まさに」を別タスクで整える
  （本統合のスコープ外の既存散文）。

```json
{"constraints": ["codd-gate 連携の完了条件（正典受入）は `! git grep _apply_codd_gate -- tools/agent-project` の exit 0 に固定する。パスは tools/agent-project のまま（_apply_codd_gate はパッケージ限定のため絞り込み不要）。広い _codd_gate|import codd_gate 述語を使う場合のみパスを agent_project/ に絞り、両者を1本の連結パターンに混ぜない。", "設計書 §4/§4.1/§4.2 と README 一貫性ゲート節の境界文言は同一に保つ: パッケージ（agent_project）は codd_gate_* を名指し・import・自動配線・依存しない／codd_gate_*.py は tools/agent-project 直下の任意 sibling 部品で欠落しても同一挙動／regression_cmd・intake_cmd の書き手は一つ（有効化=yaml・CLI／永続化=codd_gate_regression.py）。", "codd-gate 境界状態の露出口は doctor findings（doctor_findings / doctor_codd_gate_findings）に統一する。dashboard 描画を主張しない。"]}
```
