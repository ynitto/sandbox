# docs/designs/codd-gate-design.md への追記案（§4 拡張）

**切り口**: 「検出・結線・degrade は全部つながって動いている」という前のめりな書き方を避け、
**今日実際にライブな部分**（`.agent/agent-project.yaml` の静的文字列結線）と
**実装・テストは済んでいるがランタイムへの結線が未着手の部分**（検出/degrade レイヤ）を
明確に切り分けて書く。t2 の報告（`artifacts/t2/report.md`）が独立に確認した
「フォールバックは"3段"と謳われているが実装は explicit→PATH→同梱パスの構成で、
"スキル配置ディレクトリ"という独立した第3段は存在しない」という差分も踏まえ、
issue の文言をそのまま転記せず実装に合わせて書き直す。

適用先: `docs/designs/codd-gate-design.md`
挿入位置: `## 4. agent-project との結合点（オプション連携・プラグイン境界）` 節の末尾
（`agent-flow を単体で使うときは...現状は外側で必ずゲートされるため設けない。` の直後、
`## 5. codd-dev からの主な翻案（差分）` の直前）に、新しい `### 4.1` 見出しとして挿入する。

---

### 4.1 検出と no-op 縮退（`tools/agent-project/codd_gate_*.py`）

`regression_cmd`/`intake_cmd` はもともと agent-project が持つ**任意ツール向けの汎用フック**
（`agent_project/config.py` の `regression_cmd`/`intake_cmd`。codd-gate を名指ししない）。
**今日実際にライブな codd-gate 連携**は、この汎用フックへ codd-gate の起動文字列を
そのまま静的に書き込む構成（`.agent/agent-project.yaml` の `regression_cmd`/`intake_cmd`。
上表①③、本書冒頭の「有効化は設定だけ」）。この構成では **codd-gate が未インストール／
非互換でもコマンド文字列自体は変わらない**——シェルが `command not found` 等で非 0 終了し、
regression ゲートは失敗として人へ回る（intake は stdout が非 JSON になり無視される）。
つまり静的結線だけを見る限り、「無ければ何もせず素通り」ではなく「無ければ失敗として扱われる」。

これとは別に、`tools/agent-project/` 配下に codd-gate の**実在・バージョン・互換性を検出し、
使えない場合に安全側（no-op）へ縮退させる**ための独立モジュール群が実装・単体テスト済み
（`test_codd_gate_detect.py`/`test_codd_gate_routing.py`、計29 tests PASS）:

| モジュール | 責務 |
|---|---|
| `codd_gate_detect.py` | 起動 argv の解決（`resolve_codd_gate`）とバージョン・能力の実プローブ（`get_version`/`detect_capabilities`/`check_repos_schema_compat`） |
| `codd_gate_status.py` | 検出結果を no-op 縮退させる値オブジェクト `CoddGateStatus`（`build_status`/`detect_status`） |
| `codd_gate_routing.py` | `--repos`/`--repo-dir` 引数の組み立て（`build_routing_args`） |
| `codd_gate_base.py` | regression_cmd の base rev 解決（`resolve_base_rev`。`$KIRO_BASE_REV` 未注入時のフォールバック） |
| `codd_gate_debt.py` | `tasks --debt` 出力のパースとドリフト項目の正規化（intake 側の防御的パース） |

**検出のフォールバック**（`resolve_codd_gate`。`resolve_agent_flow` と対称の解決連鎖）:

1. **explicit 指定**（呼び出し側が起動パスを明示。`.py` 拡張子なら `sys.executable` 経由で起動）
2. **PATH**（`shutil.which("codd-gate")`）
3. **同梱パス**（`tools/agent-project/` の親から見た `tools/codd-gate/codd-gate.py` を
   `sys.executable` 経由で起動）

いずれでも見つからなければ `resolve_codd_gate` は `None` を返す（agent-flow の解決と異なり、
codd-gate は任意機能なので「不明な起動コマンドを推測で組み立てない」判断）。
> **表記の注意**: 「PATH → リポジトリ内スクリプト → スキル配置ディレクトリ」という3段構成で
> 語られることがあるが、実装（および対応する単体テスト・docstring）は
> **explicit → PATH → 同梱パス**の構成であり、「リポジトリ内スクリプト」と「スキル配置
> ディレクトリ」は単一の同梱パス（`tools/codd-gate/codd-gate.py`）に統合されている。
> 実環境でも `~/.claude/skills/codd-gate/` には `SKILL.md` のみでスクリプトは存在せず
> （codd-gate は pip/local-bin 型 CLI として配布）、独立したスキル配置ディレクトリを
> 第3段として検出する経路は現状ない。本書はこの実装に合わせて記述する。

**no-op 縮退**（`CoddGateStatus`）: 実在確認・バージョン取得・`MIN_SUPPORTED_VERSION`(1.0.0) 以上か・
repos.json の schema 適合、のいずれかが失敗すれば findings が1件積まれ `usable` は自動的に
`False` になる（失敗理由の種別を呼び出し側が区別する必要はない）。`usable=False` のとき
`CoddGateStatus.command(*args)` は例外を投げず `None` を返すため、呼び出し側は
`if status.command(...):` の1行分岐だけで「使えない環境では何もしない」を実現できる設計。

**現状の結線範囲（本書と実装の乖離を避けるための明記）**: 上記の検出・no-op 縮退レイヤは
**単体では実装・テスト完了**しているが、`.agent/agent-project.yaml` の `regression_cmd`/
`intake_cmd`（実際に regression/intake フックが実行する文字列。`agent_project/mr.py`/
`agent_project/model.py`）を `CoddGateStatus` 経由で動的に組み立てる、あるいは
`usable=False` のときにフック自体をスキップさせる結線（設計上 b3・c1・e1 と呼ばれる箇所）は
**未実装**。したがって現時点で codd-gate が未インストール／非互換の環境に「有効化済みの」
`.agent/agent-project.yaml` を持ち込むと、no-op 縮退はされず regression_cmd のシェル実行
自体が失敗する（agent-project 本体は落ちないが、そのタスクは規約どおり人へ回る）。
この差を埋める（`CoddGateStatus`/`build_routing_args`/`resolve_base_rev` を実際に
`cfg.regression_cmd`/`cfg.intake_cmd` の実行系へ組み込む）のは別タスクの担当として残っている。

---

## (b) 検証内容と結果

- 対象コードの実体確認: `/Users/nitto/Workspace/sandbox`（`main` ブランチ、branch は
  `agent-state` と同一リポジトリの別 worktree）で `tools/agent-project/codd_gate_{detect,status,
  routing,base,debt}.py` と `tools/agent-project/tests/test_codd_gate_{detect,routing}.py` を
  直読して確認（Read のみ、共有チェックアウトへの書き込みは一切行っていない）。
- `.agent/agent-project.yaml`（同 worktree）に `regression_cmd`/`intake_cmd` が
  codd-gate 起動文字列で既に設定済み（未コミットの作業ツリー差分）であることを `git diff` で確認。
  この静的結線を「今日ライブな連携」の根拠にした。
- `grep -rn "codd_gate" tools/agent-project/agent-project.py tools/agent-project/agent_project/*.py`
  → ヒットなし（テストファイル以外）。これを「検出/degrade レイヤがランタイムへ未結線」の
  直接的根拠にした。
- `tools/agent-project/tests/test_codd_gate_routing.py` の未コミット差分
  （`TestAgentProjectYamlWiring`）に「実際の `cfg.regression_cmd`/`cfg.intake_cmd` への
  自動配線（b3/c1/e1）は別タスクの担当」という明示コメントがあり、上記の切り分けと一致することを確認。
- 依存タスク t1（`artifacts/t1/contract.md`）・並行タスク t2（`artifacts/t2/report.md`）の
  確定仕様・独立確認と整合させた（3段フォールバックの表記齟齬は t2 が独立に同じ結論に到達）。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 「t2/t3 の確定仕様」のうち t3 の成果物（`artifacts/t3/`）はこの時点で空だったため、
  実装コードそのもの（前ラウンド r0 実装、t1/t2 が確定した契約と一致）を一次ソースとして採用した。
  t3 の成果が後で追加された場合、本addendumの「現状の結線範囲」節と矛盾が無いか要突き合わせ。
- **前提**: 挿入先は `docs/designs/codd-gate-design.md`（本書冒頭で「codd-gate の唯一の設計正典」
  と自己宣言している文書）とした。`tools/agent-project/README.md` の既存パラグラフ（234-236行、
  運用者向け「有効化は設定だけ」の説明）は今回変更しない案としたが、no-op 縮退が未結線という
  重要な運用上の注意点をREADME側にも一言足す価値はあるため、必要なら別途
  `agent-project-readme-caveat-addendum.md`（本ディレクトリに同梱）を採用されたい。
- **未解決事項**: 検出/degrade レイヤをランタイムへ実際に結線する作業（b3/c1/e1）は本タスクの
  範囲外（別タスク・実装作業）。ドキュメントは「現状こうなっている」を記述するに留めた。
- **範囲外で見つけた問題**: なし（読解のみで、範囲内で完結）。
