切り口: 境界を散文の宣言ではなく、git grep で反証可能な「不変条件＋禁止事項」の集合として定義し、『整理の完了判定』と dashboard 表示を同じ機械述語に束ねる（主観判定を排す）。

# 設計書 §4 向け境界定義（草案 / 候補 g1）

t1（gap-analysis.md）が挙げた G4「境界が規範として未明文化」・G1/G2「境界を破る自動配線の記述が同居」を、§4 冒頭に置く一枚の規範ブロックで塞ぐための草案。目的は、実装前に『agent_project パッケージは汎用フックのみ・codd_gate_* は sibling 任意部品』を機械判定できる形で固定し、整理の完了と dashboard の見せ方がぶれない基準を作ること。

---

## 4.0 プラグイン境界（規範）

**一文定義**: agent_project パッケージは E1〜E6 の汎用フックだけを提供し、codd-gate を名指ししない。codd_gate_* はそのフックへ値を供給する、パッケージの隣（sibling）に置かれた任意部品である。

### 二つの主体

- **agent_project パッケージ（`tools/agent-project/agent_project/`）＝ 汎用フックのみ。**
  verify/acceptance（E1）・regression_cmd（E2）・intake_cmd（E3）・enqueue/inbox（E4）・notify_cmd（E5）・executor（E6）という、対象を問わない外部 CLI 差し込み点を提供する。これらの口は codd-gate 固有の知識を持たない。パッケージにとって codd-gate は「フックへ渡されうる任意の値」であって、コード上の依存先ではない。

- **codd_gate_*（`tools/agent-project/` 直下の sibling 群）＝ 任意部品。**
  汎用フックへ渡す文字列を組み立て、`schemas/` の共通データ契約で入出力する隣接モジュール／CLI。パッケージ本体（`agent_project/` ディレクトリ）の内側ではなくその兄弟位置に置かれ、`agent_project.codd_gate_*` ではなくトップレベルの sibling モジュールとしてのみ存在する。任意・可搬で、丸ごと削除してもパッケージは同一挙動を保つ。

> sibling である根拠は配置に現れる。`codd_gate_*.py` はパッケージ・ディレクトリ `agent_project/` の外、その隣に並ぶ。ゆえにパッケージから見れば同梱物ではなく「隣にあるかもしれない外部部品」であり、参照経路は共通スキーマと汎用フックの値だけになる。

### 禁止事項（agent_project パッケージが持ってはならないもの）

1. **codd-gate 専用の配線関数を持たない。** `_apply_codd_gate_auto_wiring` / `_apply_codd_gate` をはじめ `_codd_gate*` を冠する私的関数・メソッド・定数を `agent_project/` 配下に定義しない。起動経路（`build_config()` 等）から codd-gate の実在・バージョン・能力を自動検出・自動配線しない。
2. **codd_gate_* を import しない。** `import codd_gate_wiring` / `from codd_gate_debt import …` などの sibling import をパッケージ内から書かない。パッケージ・コードは codd-gate を名指ししない。
3. **人専有ファイルへ機械が codd-gate 由来の値を書かない。** `.agent/agent-project.yaml` は従来どおり自動配線・自動書換の対象外（`agent_project/state.py` の `_HUMAN_OWNED_STATE_FILES`）。

### 許容される結合（この 2 経路だけ）

- **共通データ契約**: 入力は `schemas/repos.schema.json` 準拠の `<root>/repos.json`、出力は `schemas/task.schema.json` 準拠の `codd-gate tasks --debt` の stdout。identity (url, path, base) を共有するだけで、コードは共有しない。
- **汎用フックへの値供給**: E1〜E3 に codd-gate のコマンド文字列を「値として」与える。値を書くのは人、または sibling CLI の `codd_gate_regression.py`（`python3 tools/agent-project/codd_gate_regression.py --config .agent/agent-project.yaml` の冪等 upsert）。有効化は設定だけで完結し、パッケージ本体のコード分岐を要さない。

### 欠落時の挙動（可搬性の保証）

- codd_gate_* が隣に無い／削除された場合も、パッケージは import エラーを起こさず同一挙動で動く（パッケージは sibling を参照しないため）。t1-G5 が指摘した「モジュール自体の欠落時の無害性」をここで明示保証する。
- 有効化しても codd-gate バイナリが未検出・バージョン不適合・schema 非互換なら、値を供給する sibling 側で no-op 縮退する（`CoddGateStatus.usable=False`）。「止める方向（E2）／足す方向（E3）」の非対称は codd-gate 側の設計として保たれる。

---

## §1 不変条件への追記案（第 7 条）

t1-G4 の是正。既存 5 か条（+ codd-gate 側 6 か条）と同じ体裁で、境界を規範として固定する。

> 7. **連携部品を名指ししない（プラグイン境界）。** agent_project パッケージは E1〜E6 の汎用フックのみを提供し、codd-gate を含むいかなる連携部品もコードから名指し・import・自動配線しない。codd_gate_* は `schemas/` の共通契約と汎用フックへの値供給だけで結合する sibling 任意部品であり、欠落・削除してもパッケージは同一挙動を保つ。

---

## 完了判定（反証可能な受入述語）

境界の遵守は次の述語で機械判定する。dashboard の「codd-gate 連携＝整理済み」表示も同じ述語に束ねる（散文レビューではなく grep の exit code を単一の真偽源にする）。

- **P1（canonical / run 受入）**: `! git grep -n '_apply_codd_gate' -- tools/agent-project/`
  codd-gate 専用の自動配線関数が存在しない。
- **P2（禁止事項 1 の完全形）**: `! git grep -nE '_codd_gate' -- tools/agent-project/agent_project/`
  パッケージ配下に codd-gate を冠する私的シンボルが無い。
- **P3（禁止事項 2）**: `! git grep -nE '^\s*(import|from)\s+codd_gate' -- tools/agent-project/agent_project/`
  パッケージ内から sibling を import していない。
- **P4（強境界 / 任意採用）**: `! git grep -niE 'codd.?gate' -- tools/agent-project/agent_project/`
  パッケージ・コードが codd-gate をいかなる形でも名指ししない。P2/P3 を包含する最強述語。doctor.py／model.py の sibling import（後述の未解決）をどう裁くかで採否が決まる。
- **P5（可搬性 / 挙動述語）**: `tools/agent-project/codd_gate_*.py` を全て退避してもパッケージのテストが緑。sibling 欠落時の無害性を実挙動で担保する。

正典の受入は P1。P2・P3 は P1 と同じ「名指ししない」原則の完全形で、境界を実装後も固定するために追加を推奨する。P4 は評価役が境界の厳密度を一段上げたい場合の上位互換。

---

## 検証内容と結果

- 完了条件（キーワード 4 語）の充足を本文で確認: 「agent_project パッケージ」「汎用フック」「sibling」「_apply_codd_gate」を境界定義・禁止事項・受入述語に含めた。
- 正典との整合を実読で照合。§4 冒頭 L241–244「本体は無改造・E1/E2/E3 を使う」・§4 表 L246–252（汎用フックに値を与える正しいモデル）・§4.1 L260–261「部品として存在」・§5 L306–329（差し込み点選択の妥当性）は t1 で「アライン済み」とされた記述で、本草案はこれらと矛盾しない上位の規範として書いた。逆に本草案が置換を要求するのは §4.1 L284–295（現在地＝build_config 自動配線）と README L279–282（build_config 自動検出・自動埋め）で、これは t1-G1/G2 の是正対象そのもの。
- 受入述語をコードで実測突き合わせ（t1 の実測を再利用）。現行は `agent_project/configfile.py:201,376`（`_apply_codd_gate_auto_wiring`）が P1 を、`doctor.py:287–314`（`codd_gate_wiring` import）と `model.py:494–552`（`codd_gate_debt` import）が P3/P4 を、それぞれ現時点では満たさない。つまり本草案は「実装後に成立させるべき目標述語」を定義しており、現状の反証点を正しく指している。
- ドキュメント草案のためテスト／型チェックは非対象。文章は slop-police の観点（主体の明示・命題型見出しの回避・装飾ダッシュの抑制）で通読・調整した。

## 採用した前提・未解決・範囲外

- **採用した前提**:
  - 目標境界を t1 と同一に固定（agent_project ＝ E1〜E6 の口のみ・codd-gate 非名指し／codd_gate_* ＝ 可搬な任意 sibling）。裏付けは run 受入 `! git grep _apply_codd_gate`。
  - 「sibling」を配置由来の語として定義した（`agent_project/` パッケージ・ディレクトリの外・その隣にある `codd_gate_*.py`）。この配置定義が「任意・可搬・非依存」を構造的に裏付けるため、境界の中核に据えた。
  - 本タスクは候補生成。実際の §4 差し込み位置（4.0 として冒頭か、§1 第 7 条か、両方か）の確定は後続の統合／評価タスクに委ねる。
- **未解決事項（評価役／実装タスクの判断待ち）**:
  - `doctor.py`（`codd_gate_wiring`）・`model.py`（`codd_gate_debt`）の sibling import を境界違反として除去するか、「汎用な optional-import 機構」に言い換えて温存するか。本草案の禁止事項 2・受入 P3/P4 は「除去」に倒した書き方をしている。温存を選ぶなら禁止事項 2 を「codd-gate を名指しする自動配線を書かない（optional-import 自体は汎用機構として許容）」へ緩め、P4 を落として P1〜P3 の範囲を狭める必要がある。ここは境界の厳密度そのものの意思決定。
- **範囲外で気づいた点（手を出さず記載。@followup）**:
  - `@followup` 実装タスク: 本草案を §4 に反映する際、§4.1 L284–295「現在地」段落と README L279–286 の書換に加え、コード側 `_apply_codd_gate_auto_wiring` の除去が受入（P1）成立に必要（本タスクは docs 草案のみ）。
  - `@followup` §4.1 見出し「自動検出レイヤ」／リード「機械化する補助モジュール」（t1-G3）は、本草案の sibling 任意部品像と逆向きのフレーミングのため、語彙合わせが要る。統合タスクで見出しを「隣接する任意部品（codd_gate_*）」系へ寄せることを提案。
  - workspace 制約により本タスクでは repo を一切書き換えていない（成果物は bus artifact のみ・調査/生成タスク）。
