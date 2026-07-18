# t1 ギャップ列挙 — codd-gate 連携の目標境界 vs 現行ドキュメント

対象: `docs/designs/codd-gate-design.md` §4（差し込み点 E1–E3）・§4.1（自動検出レイヤ）と
`tools/agent-project/README.md` 一貫性ゲート節（L272–286）。

## 採用した前提（解釈の固定）

目標境界『agent_project=汎用フックのみ／codd_gate_*=sibling 任意部品』を次の 2 条と読んだ。
run の受入 `! git grep … _apply_codd_gate…`（＝この関数が消えること）を裏付けとする。

- **agent_project（パッケージ）= 汎用フックのみ**: E1–E6 の口（verify/acceptance・regression_cmd・
  intake_cmd・inbox/enqueue・notify_cmd・executor）だけを提供し、パッケージ内から codd-gate を
  名指し・import・自動配線しない。設計 §4 冒頭の「agent-project 本体は無改造」がこれに当たる。
- **codd_gate_* = sibling 任意部品**: `tools/agent-project/` 直下の隣接モジュール／CLI。任意・可搬・
  no-op 縮退。欠落・削除してもパッケージは同一挙動。結合は `schemas/` の共通データ契約と、
  人（または `codd_gate_regression.py`）が汎用フックへ書く値だけ。

## 現状（コード実測、判断の根拠）

境界違反はパッケージ内 3 箇所に集中する（本タスクは調査のみ・実装は触らない）。
- `agent_project/configfile.py:201,376` — `_apply_codd_gate_auto_wiring()` を定義し `build_config()` が呼ぶ。
- `agent_project/doctor.py:287–314,528` — `doctor_codd_gate_findings` が `codd_gate_wiring` を sibling import。
- `agent_project/model.py:494–552` — intake が `codd_gate_debt` を sibling import。

## ギャップ一覧（矛盾 / 不足 / 曖昧）

### G1【矛盾・最重要】§4.1「現在地（結線状況）」が build_config→codd_gate_wiring の自動配線を正典として記述
- 該当: design §4.1 **L284–295**（特に「`agent_project.configfile.build_config()`……が
  `codd_gate_wiring.detect_wiring()` を呼んで `cfg.regression_cmd`/`cfg.intake_cmd` を**メモリ上で**
  自動配線する（`_apply_codd_gate_auto_wiring`）」）。
- 問題: 目標境界では build_config（＝agent-project 本体）は codd-gate を名指ししない。この記述は
  目標と正面から矛盾し、同 §4 冒頭 L241–242「agent-project 本体は無改造」とも自己矛盾する。
  受入 `! git grep _apply_codd_gate` はこの関数の消滅を要求 → 段落ごと削除／書換が必要。

### G2【矛盾】README 一貫性ゲート節が「結合は共通スキーマのみ」と宣言しつつ build_config 自動検出・自動埋めを記述
- 該当: README **L272–286**。L274「結合は共通スキーマ（`schemas/`）のみ」（宣言）↔
  L279–282「起動時の Config 生成（`build_config`）が codd-gate の実在・バージョン・repos.json 互換性を
  自動検出し、`regression_cmd`/`intake_cmd` が未設定のときだけメモリ上で自動的に埋める」（違反）。
- 問題: 同一段落内で境界を宣言かつ破っている。目標に合わせるなら「有効化は設定だけ（人 or
  `codd_gate_regression.py` が汎用フックへ値を入れる）」へ一本化し、build_config 自動配線の記述を除去。

### G3【矛盾・曖昧】§4.1 見出し「自動検出レイヤ」とリード文「機械化するための補助モジュール」というフレーミング
- 該当: design §4.1 **L258 見出し・L260–263**。
- 問題: 「自動検出レイヤ」という命名は codd_gate_* を agent-project に統合された一枚のレイヤとして
  提示し、目標の「sibling 任意部品」像とズレる。L260「今日まで人が手で書く前提だった……その
  組み立てを機械化する」も、目標（人／`codd_gate_regression.py` が汎用フックへ書くのが正）と
  逆向きの物語。見出し・リードを「隣接する任意部品（codd_gate_*）」の語彙へ寄せる必要。

### G4【不足】目標境界そのものが規範（不変条件）として明文化されていない
- 該当: design §1「不変条件」**L78–92**（該当条項なし）・§4 全体。
- 問題: 「agent_project パッケージは codd-gate を名指し・import・自動配線しない（汎用フックのみ）」
  「codd_gate_* は任意部品で、欠落・削除してもパッケージは同一挙動」という不変条件が規範として
  どこにも書かれていない。§4 冒頭「本体無改造」は散文の一言に留まり、§4.1 現在地の記述に
  実質上書きされている。境界を不変条件として固定する記述が不足。

### G5【不足・曖昧】「任意部品が欠落／削除された場合のパッケージ挙動」が明示されていない
- 該当: design §4.1 **L280–282**（`usable=False` の no-op 縮退は「codd-gate バイナリを検出できない
  環境」の話に留まる）。
- 問題: 「codd-gate バイナリ未検出→no-op」は書くが、「codd_gate_* モジュール自体が隣に無い／
  消された場合もパッケージは無害に動く（依存しない）」という任意部品の可搬性を、目標境界の
  言葉で保証していない。build_config 自動配線を外した後、パッケージが codd_gate_* に依存しない
  ことを明記する余地。

### G6【曖昧】§4 が「差し込み点＝汎用」と「codd-gate 専用配線」を同一節に地続きで混在
- 該当: design §4 表 **L246–252**（汎用の E1/E2/E3 に codd-gate 文字列を値として与える正しいモデル）と
  §4.1 現在地（codd-gate 専用の自動配線）が同一節に並ぶ。
- 問題: 読者に「汎用フック＋任意の値」なのか「codd-gate 専用の統合レイヤ」なのか判別が付きにくい。
  目標では前者に一本化し、後者（自動配線）を切り離す構成整理が要る。

## アライン済み（据え置き・変更不要）

- design §4 冒頭 L241–244「agent-project 本体は無改造」「E1/E2/E3 の外部 CLI 差し込み点を使う」— 境界そのもの。
- design §4 表 L246–252 — 汎用フックに codd-gate 文字列を値として与える正しいモデル。
- design §4.1 L260–261「`tools/agent-project/` 直下に**部品として**存在」— sibling 位置の記述はアライン。
- design §5「差し込み点選択の妥当性」L306–329 — 汎用フックの選択理由。境界と整合。
- README L272–274「完全独立のツール」「結合は共通スキーマのみ」— 宣言自体は目標どおり（G2 は後続の破りが問題）。
- README L283–284 `codd_gate_regression.py --config …`（sibling CLI で永続化）— 任意部品の正しい使い方。
- README L93 / L512 repos.json 自動生成は「codd-gate 等」と汎用例示 — repos.json は汎用レジストリで境界的に無問題。
