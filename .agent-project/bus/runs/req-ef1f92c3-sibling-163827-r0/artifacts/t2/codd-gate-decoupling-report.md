# t2: codd_gate_wiring / codd_gate_base の自動配線切り離し — 作業報告

対象: `tools/agent-project/codd_gate_wiring.py` / `codd_gate_base.py`（+ 各テスト）。
`agent_project/` パッケージ内と dashboard は一切変更していない。

## (a) 成果

### 1. 切り離しの実体 — 契約名を `def` から別名へ

`agent_project/hooks.py:83` の sibling 自動走査は、import 前にソーステキストへ
`^def <必須属性>\s*\(` の前置フィルタをかける。従来の `codd_gate_wiring` は
`def detect_wiring(` / `def doctor_findings(` を持つため、**零設定でこのフィルタに当選し、
利用者が何も書かなくても本体 doctor へ繋がっていた**。

実装の名前をモジュール自身の語彙へ戻し（`detect_wiring` → `probe_wiring`、
`doctor_findings` → `render_findings`）、契約名はファイル末尾の**別名**として公開した。

```python
detect_wiring = probe_wiring
doctor_findings = render_findings
```

結果、`hasattr` を見る明示指定（`hooks: {wiring: codd_gate_wiring}`）では従来どおり解決でき、
ソーステキストを見る自動走査では候補にすら上がらない。これで「パッケージ外の sibling に置いてある」
という配置と「設定ファイルに書いて有効化する」という手順が一致する。

`agent_project/hooks.py` へは手を入れていない（本体側で自動走査を止める方が直接的だが、
タスクのスコープ外かつ他プロバイダの経路まで塞ぐため採らなかった）。

### 2. 零設定の利用者向けの受け皿 — CLI 所見出力を追加

自動配線を切ると「設定を書かずに現状を知る」手段が消えるため、`codd_gate_wiring.py` に
`main()` を追加した（タスク定義の「（必要なら）CLI 所見の出力」）。

```
python3 codd_gate_wiring.py [--config .agent/agent-project.yaml] [--repos <path>] [--codd-gate <path>]
```

検出結果・結線の有無・所見を JSON で stdout へ出すだけで**どこへも書かない**。所見の有無は終了
コードに反映しない（一貫性ゲートは任意機能で、未結線は壊れた状態ではない）。yaml の1行スカラ読みは
`_read_yaml_value`（PyYAML 非依存）、`root:` → `<root>/repos.json` の推定は
`codd_gate_regression.infer_default_repos_path` を CLI の中でだけ import して再利用する
（ライブラリ import 時の依存は増やさない）。

### 3. `codd_gate_base.py` — 存置。docstring を新境界へ

コードは変更なし（`resolve_base_rev` / `FALLBACK_BASE_REV`）。docstring から
`agent-project.py:4906-` 等の**分割で全滅した行番号参照**と「同一 run の別タスクの責務」という
run ローカルな語を外し、「誰も自動では掴まない・明示 import 専用」という現在地に書き換えた。

新規に `tests/test_codd_gate_base.py`（6 ケース）を追加。

### 4. 責務の最終形

| モジュール | 責務 | 本体への繋がり方 |
|---|---|---|
| `codd_gate_wiring` | 検出（`probe_wiring`）・結線判定・推奨文字列・所見整形（`render_findings`）・CLI | `hooks:` の明示指定のみ（別名 2 本） |
| `codd_gate_base` | base rev 解決（純粋関数） | 明示 import のみ |

## (b) 検証

| 検証 | 結果 |
|---|---|
| `python3 -m unittest discover -s tests -p 'test_codd_gate_*.py'` | **105 tests OK**（t1 時点 81 → 他タスク分 +18 → 本タスク +6） |
| 全体 `python3 -m unittest discover -s tests` | 832 tests / **failures=2**。いずれも codd_gate 無関係の環境依存（下記） |
| CLI 実行 `python3 codd_gate_wiring.py --config /nonexistent/...` | exit=0。同梱 codd-gate を検出し info 所見 2 件（regression/intake の推奨文字列）を JSON 出力 |
| 零設定の end-to-end `doctor_wiring_findings(cfg with hooks={})` | **`[]`**（＝自動配線が実際に切れている） |
| 明示指定 `hooks={'wiring':'codd_gate_wiring'}` での `_hook_provider` | `wiring.detect` / `wiring.findings` とも `codd_gate_wiring` module を解決（＝明示経路は生きている） |

全体スイートの failures 2 件は本変更と無関係（変更ファイルを import しないテスト）:

- `TestDaemonRouting.test_kf_base_passes_flow_config` — macOS の `/var` → `/private/var` シンボリックリンクで
  `resolve()` 後のパスが一致しない
- `TestJournalRotation.test_rotation_archives_and_starts_fresh` — アーカイブ連番の辞書順ソート
  （`.1 .10 .11 ... .2`）に起因。ホスト名混じりのファイル名で再現

### 回帰テストで固定した契約

`tests/test_codd_gate_wiring.py::TestHookResolution`:

- `test_sibling_scan_does_not_select_this_module_when_unconfigured` — 零設定で当選しない（切り離しの本体）
- `test_no_sibling_provides_the_capabilities_by_autodetect` — **どの sibling も**自動走査に当選しない
  （誰かが `def detect_wiring(` を書けば自動配線が復活するため、走査そのものの空振りを固定）
- `test_this_module_satisfies_the_declared_capability_contract` — 明示指定側の契約は満たす
- `test_contract_names_are_aliases_of_the_modules_own_functions` — 別名が実体と同一
  （片方だけ直して契約名が古い実装を指す事故の防止）

`tests/test_codd_gate_base.py::TestNotAutoWired` — `codd_gate_base` がフック契約の属性を一切持たない。

## (c) 前提・判断・申し送り

### 判断: 宙に浮く公開関数は「削除せず、明示呼び出し用 API として残す」

タスクが報告への明記を求めた判断。3 つの対象すべてで**存置**を選んだ。

**① `detect_wiring` / `doctor_findings`（`codd_gate_wiring`）— 残す（別名として）**

自動走査からは外したが、`agent_project/hooks.py:15-18` の `HOOK_CAPABILITIES` が要求する属性名そのもの。
消すと `hooks:` の明示指定まで解決不能になり、「設定で有効化する」という新しい手順ごと壊れる。
切りたいのは**零設定で勝手に繋がること**であって、本体との接続点そのものではない。

**② `probe_wiring` / `render_findings` の本体 — 残す（改名のみ）**

CLI と単体テストという実呼び出し元がある。

**③ `resolve_base_rev`（`codd_gate_base`）— 残す**

t1 が指摘したとおり呼び出し元ゼロで、削除も筋は通る。それでも残した理由:

1. **埋めている穴が消えていない。** 推奨文字列（`recommend_regression_cmd` /
   `build_regression_cmd`）は `--base "$KIRO_BASE_REV"` をシェル変数参照のまま埋め込む設計で、
   本体が baseline を取れない状況（非 git ワークスペース・初回コミット前）では空文字へ展開され
   `codd-gate --base ""` が失敗する。この穴は本タスクの切り離しで塞がっていない。
2. **本タスクの責務定義に収まる。** 「推奨文字列の生成」の一部であり、変数展開に頼らず具体的な
   rev を埋めた regression_cmd を組み立てたい呼び出し元が明示 import して使える。
3. **削除のコストが非対称。** 純粋関数 23 行・依存は stdlib のみで、置いておく負債が小さい。
   一方、消してから同じ穴に当たると設計判断（優先順位 3 段）から作り直しになる。

ただし「呼び出し元もテストも無い API」は次に読む人には削除候補にしか見えないため、
**単体テストを新規に追加して契約を固定した**（`tests/test_codd_gate_base.py`）。存置の根拠を
コードとして残す方が、docstring の主張だけより強い。

### 採用した前提

- 「自動配線経路の切り離し」を **`agent_project/hooks.py` の sibling 自動走査に当選しないこと**と解した。
  t1 が確認したとおり `build_config` のメモリ上自動配線（`_apply_codd_gate_auto_wiring`）は既に除去済みで、
  コード上に残っていた最後の自動経路がこの走査だったため。
- 切り離しの手段として、本体側（`hooks.py` の走査ロジック）ではなく sibling 側（契約名の公開方法）を
  変えた。タスクが「agent_project パッケージ内への再結合と dashboard の変更は行わない」と定めており、
  本体の走査を消すと codd_gate 以外のプロバイダの経路まで塞ぐため。
- 「（必要なら）CLI 所見の出力」を**必要**と判断した。自動配線を切ると零設定の利用者が現状を知る
  手段が無くなり、`hooks:` を書く前に「そもそも codd-gate が使えるのか」を確認できないため。

### 範囲外で見つけた問題（手を出していない）

- @followup **README:281-283 が実挙動とずれる**（t1 §5-③ と同根、かつ本変更で 1 段深くなった）。
  「未結線なら doctor が推奨コマンド文字列を finding として提示する」は、**いまや `hooks:` を書いた
  環境でのみ**成立する。README には `hooks:` 設定キーの説明が一切無いため、現状のままでは有効化手順が
  README から辿れない。追記候補: `hooks: {wiring: codd_gate_wiring}` の記法と、設定なしで見る
  `python3 codd_gate_wiring.py` の CLI。
- @followup `docs/designs/codd-gate-design.md:284-304` が stale（t1 §5-①）。差し替え時は
  「自動走査には**載らない**／明示指定と CLI の 2 経路」という本タスクの結論を反映すること。
- @followup `agent_project/config.py:127` のコメント「未指定なら sibling ディレクトリを能力で走査して
  引き当てる」は機構としては正しいが、実際に当選する sibling は現在ゼロ。パッケージ内なので触っていない。
- @followup 全体スイートの環境依存 failure 2 件（上記 (b)）。macOS + 特定ホスト名で恒常的に落ちる。
- @followup `codd_gate_debt.py` は依然として本番経路からの呼び出し元ゼロ。docstring が意図的な存置と
  明記しているため本タスクでは触っていないが、`codd_gate_base` と同じ「明示 import 専用 API」として
  扱うかの判断は未了。
