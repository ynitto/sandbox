# §4.1「現在地」の実装追随 — 切り口: 有効化の入口を数え上げて閉じる

設計と実装の食い違いを「文言の直し」ではなく **入口の数え上げ** として扱った。利用者が
codd-gate 連携を有効にできる経路を実測で列挙し、3つしか無いことを本文で閉じる。閉じた集合で
書けば、消えた自動配線が「書き忘れ」か「意図的な不在」かが読者に判別できる。

## 変更したファイル

`docs/designs/codd-gate-design.md` の §4.1 のみ（モジュール表2行、「現在地」ブロック全体、
ヘッダの最終更新日）。tools/agent-project 配下のコードは無変更。

## 更新前後の主張の差

| 論点 | 更新前の記述 | 実装の現在地 |
|---|---|---|
| `build_config` の自動配線 | `_apply_codd_gate_auto_wiring` が repos.json 実在時に `cfg.regression_cmd`/`intake_cmd` をメモリ上で埋める | 削除済み。パッケージ内に `codd_gate` の固有名が1つも無い |
| 有効化の入口 | 自動配線＋手書き＋`codd_gate_regression.py` の3系統が併存 | 明示設定のみ。yaml 手書き / `codd_gate_regression.py --config` / `hooks:` 指定の3つ |
| doctor からの到達 | （記述なし。自動配線の副次として繋がる前提） | `hooks: {wiring: codd_gate_wiring}` を書いたときだけ到達 |
| `codd_gate_wiring` の公開名 | `detect_wiring()` / `doctor_findings()` が実体 | 実体は `probe_wiring` / `render_findings`。契約名は末尾の別名（sibling 自動走査に載らないため） |
| `codd_gate_regression.py` の契約 | 「人・install 手順が実行する生成ツール」まで | 終了コード 0/1/2/3、`--config` は実在必須（t3 の CLI 化を反映） |
| no-op 縮退の所在 | 自動配線が「未検出なら最初から設定されない」を保証 | 各入口へ分散。CLI は 3 で停止、doctor は空へ畳む。E2 の「未インストール環境で block」は残る |

## 実測による裏取り

| # | 確認内容 | 方法 | 結果 |
|---|---|---|---|
| 1 | パッケージに codd-gate 依存が無い | `grep -rn codd_gate agent_project/` | 該当はコメント・docstring のみ。import 0 件 |
| 2 | 自動配線の不在 | `TestCoddGateNoAutoWiring`（`hasattr(km, "_apply_codd_gate_auto_wiring")` が False、repos.json 実在でも cfg が None） | PASS |
| 3 | sibling 自動走査が `codd_gate_wiring` を拾わない | `_hook_scan_siblings(("detect_wiring",))` / `(("doctor_findings",))` | 両方 `None` |
| 4 | 明示指定でだけ解決する | `hooks={'wiring':'codd_gate_wiring'}` で `_hook_provider('wiring.detect', cfg)` | module を解決 |
| 5 | `codd_gate_wiring.py` CLI は読むだけ・常に 0 | 不在パスを `--config` に渡して実行 | JSON 4キー＋findings 2件、`rc=0`、ファイル未作成 |
| 6 | 人専有ファイルの一覧 | `state.py` の `_HUMAN_OWNED_STATE_FILES` | `("agent-flow.yaml", "agent-project.yaml")` |
| 7 | 終了コード表 | `codd_gate_regression.py` の `EXIT_*` と `_EPILOG` | 0/1/2/3 が本文と一致 |

テスト: `pytest tests/ -k codd` → **115 passed**。全体 → **842 passed / 2 failed**（t3 報告と同一の
環境依存2件。journal ローテーションのアーカイブ連番と macOS の `/var`→`/private/var`。どちらも
docs/ を読まない）。

## 前提と未解決

**採用した前提**: 「明示設定のみ」を「`cfg.regression_cmd`/`intake_cmd` に機械が値を入れる経路が
無いこと」と解した。doctor のフック解決は `hooks:` 未指定なら sibling を走査する汎用機構なので
文字どおりの「明示のみ」ではないが、`codd_gate_wiring` は別名公開で走査から意図的に外れており、
codd-gate に関する限り明示設定が唯一の入口になる。この二段構え（汎用機構は自動検出を持つ／
codd-gate 側が自ら降りている）を本文に書き分けた。

**範囲外で見つけた問題**:

- @followup `tools/agent-project/README.md` の一貫性ゲート節は「結線できているかは doctor が見る」と
  書くが、`hooks: {wiring: codd_gate_wiring}` を書かない限り doctor はこのレイヤに到達しない。
  README は別タスクの担当範囲のため触っていない。
- @followup `agent-project.yaml.example` の `hooks:` コメントは「通常は書かなくてよい。未指定なら
  sibling を走査して自動検出する」とだけ述べる。汎用の説明としては正しいが、直下の例に挙がっている
  `codd_gate_wiring` は自動検出されないので、読者が誤読しうる。
- @followup `intake_cmd` に注入 CLI が無い非対称は設計本文にも明記したが、解消はしていない（t3 の
  followup と同じ）。
