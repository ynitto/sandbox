# t3〜t6 統合レポート（synth / r4）

対象ブランチ `ap/agent_project-codd_gate-163827`、統合の基点 `da915dc8`（t6 まで適用済み）。
変更は `tools/agent-project` 配下のみ。

---

## 1. 4系統は「揃っていなかった」のではなく、揃える対象が1つしかなかった

タスクは「フック名・解決順序・フォールバック挙動が4系統で揃っているか」を突き合わせよと言うが、
現物を読むと **module フックを持つ系統は wiring 1つだけ** だった。内訳:

| 系統 | 差し込み点 | `_hook_provider` を呼ぶか |
|---|---|---|
| configfile (t3) | `hooks:` の正規化のみ。解決はしない | 呼ばない |
| doctor (t4) | `wiring.detect` / `wiring.findings` | **呼ぶ（唯一）** |
| model / debt (t5) | `intake_cmd`（プロセス境界） | 呼ばない |
| intake + tests (t6) | 同上。パースは本体同梱 | 呼ばない |

これは t2 の設計判断どおりで、「debt にも module フックを置いて3系統を対称にする」案は
**却下済み**（差し込み点が二重になり片方が必ず遊ぶ）。したがって統一作業の実体は「4つを揃える」
ではなく「唯一の解決器が全消費者で同じ規約で使われているか」の確認になる。

実測での確認: パッケージ内の `_hook_provider` 呼び出しは `doctor.py:324,325` の2箇所だけで、
どちらも `cfg` を渡している（省略すると設定明示が効かない）。`importlib` を各所で呼ぶ抜け道は無い
（契約 §0.3）。フック名（`wiring.detect` / `wiring.findings`）は `HOOK_CAPABILITIES` が単一の正典で、
表と呼び出し側の片側だけ改名すると黙って no-op へ落ちるため、その一致をテストで固定した
（`test_capability_keys_used_by_callers_exist_in_the_table`）。

## 2. 統合で直したズレ — 型不正の warn が二経路にあった

t3（configfile）と t4（doctor）が、同じ「`hooks:` の型が壊れている」を**別々の層で独立に**
検出していた。t4 完了報告の「契約 §4 から拡張した」がこれで、結果として

- `hooks.py:_hook_resolution_error` が型を見て warn 用の理由文字列を作る
- `doctor.py:_hook_misconfig_findings` も型を見て warn を作る

の二重定義になり、gate の minor-1（`doctor.py:300` の分岐を `if False:` にしてもテストが緑）が
まさにこの重複の症状だった。片方を殺してももう片方が同じ severity の warn を出すので、テストから
分岐を識別できない。

**直し方**: 型の検出を**生の設定値を持つ層（configfile）に一本化**した。`_hooks_config_error` が
捨てた理由を1行にして `cfg.hooks_error` へ残し、doctor はそれを読むだけにする。`hooks.py` からは
型判定を落とし、「読めた名前が解決できない」だけを見る責務に戻した。

| 層 | 見るもの | 残すもの |
|---|---|---|
| `configfile._normalize_hooks` / `_hooks_config_error` | 生の設定値の型 | `cfg.hooks` / `cfg.hooks_error` |
| `hooks._hook_resolution_error` | 読めた名前が解決できたか | 理由文字列 |
| `doctor._hook_misconfig_findings` | 上の2つ | finding（warn） |

副作用として t3 が「契約内在の穴」として申し送った未解決事項（`hooks: {wiring: 123}` が無言で
自動検出へ落ちる）も閉じた。値が module 名でないキーだけを落とし、読める指定は生かす。
所見は**自動検出がたまたま当たっても出す**——書いた設定が効いていない事実は、代わりが
見つかったかどうかとは無関係だから。

## 3. gate fail-1 の決定 — 自動配線は復活させない（破壊的変更として受ける）

gate は「configfile 配線 → doctor findings → model debt → regression gate の鎖が main どおりに
成立しない」を fail とし、(a) 破壊的変更として受け入れる / (b) `_hook_provider` 経由で等価を戻す、
の二択を設計の所有者へ差し戻していた。**(a) を採った。**

(b) は grep 条件を満たすし、既存環境の連携も保つ。それでも採らなかったのは、**消したかったのが
名前ではなく結合そのもの**だから。起動時に外部ツールの有無を見て自分の中核設定を書き換える構造は
固有名を隠しても残り、設定ファイルに書いていない値が実行時に生えてくる状態は変わらない。
フック経由にすると、その追えなさに「本体は名前を知らない」という体裁が付くぶんかえって悪い。
元要求（「設計の『本体は無改造・差し込み点のみ』をコードで真にする」）とも (b) は正面から衝突する。

代償は移行の一手間で1回で終わる。導線を2つ用意した:

- `README.md` に移行ノート（後方非互換であること、黙って止まるもの、戻し方）を追加
- `HOOKS.md` に決定と却下理由（D/E 表）を記録

**未結線所見の severity は `info` に据え置いた。** gate は (a) の一部として info→warn を求めたが、
所見を作っているのはプロバイダ側（`codd_gate_wiring.doctor_findings`）で、本体は `judgment` を
不透明として扱う以上そこへ手を入れる口が無い。契約 §0.5 も sibling の変更を禁じている。加えて
`info` は新規インストールにとっては正しい——連携は任意機能で、使っていない環境で warn を出し
続ければ誤報になる。warn が欲しいのは移行の瞬間だけで、それは severity ではなくリリースノートが
担う範囲。@followup として残した。

## 4. 検証

| 項目 | 結果 |
|---|---|
| 全体スイート | **742 tests / failures=3** — 契約 §5 が合格条件とした main 由来の3件のみ（`TestDaemonRouting.test_kf_base_passes_flow_config` / `TestJournalRotation.test_rotation_archives_and_starts_fresh` / `TestProjectLayer.test_version_inherits_master_charter`） |
| タスクの完了条件（逐語） | **PASS**（受入3テスト OK ＋ 受入 grep 0 hit） |
| 厳格 grep `codd_gate`（パッケージ内） | **0 hit** |
| ミューテーション再検査 | **6/6 KILLED**（gate 生存の4件＋統合で入れ替えた型不正経路2件。`mutation_recheck.py`） |
| スコープ | 変更は全て `tools/agent-project` 配下 |

gate が生存と報告した4件の内訳と現状:

| gate | 壊した性質 | 統合後 |
|---|---|---|
| fail-2 | `id: 0` を潰す素直な書き方へ退行 | KILLED |
| minor-1 | hooks 型不正の warn を出さない | KILLED |
| minor-2a | `sorted()` 撤去（走査順が非決定に） | KILLED |
| minor-2b | `_` 始まり／非識別子の除外を撤去 | KILLED |

## 5. 入力の誤りとして扱ったもの

**gate の `e2e_probe.py` の C セクションは shape が違う。** `{"items": [...]}` を
`_parse_intake_records` へ渡しているが、この形は main の `codd_gate_debt.parse_debt_output` でも
同じく 0 件＋`title が空/欠落` エラーになる（両者とも `data if isinstance(data, list) else [data]`
で、`items` キーを開かない）。sibling 側の docstring が「`codd-gate.py` の `_emit_tasks` は常に
array を吐く」と書いており、配列が実際の契約。**プローブ側の誤りで、退行ではない。** 後続が
このプローブ出力だけを見て「debt intake が壊れた」と誤読しないよう記録する（@followup）。

**t3 が契約 §0.5 抵触として申し送った sibling の変更（`codd_gate_wiring.py` /
`codd_gate_debt.py`）は docstring のみ**で、振る舞いは1行も変わっていない。しかも内容は削除済み
シンボル（`_codd_gate_debt_module`）への参照を消すもので、放置すれば docs が実在しない名前を
指し続ける。r0 の判断を妥当として受け入れ、差し戻さない。
