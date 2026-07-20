# 新境界の統合説明 — sibling 自動検出レイヤと利用手順

## 一言でいうと

codd-gate 連携は「起動しただけでは繋がらない」に統一した。`agent_project` パッケージは codd-gate
という固有名を持たず、検出・推奨文字列・yaml 注入・所見の整形はすべてパッケージ**外**の
`tools/agent-project/codd_gate_*.py`（sibling）が担う。両者を結ぶのは利用者が書く明示設定だけで、
零設定で勝手に繋がる経路は残していない。

## 境界の形

**パッケージ内（`agent_project/`）が持つもの** — codd-gate を知らない汎用の差し込み点だけ。

- `regression_cmd` / `intake_cmd`: 任意の外部コマンドを載せる文字列キー。値は設定ファイル・CLI・
  既定のいずれかがそのまま入り、起動時に何かが補われることはない。以前あった
  `configfile.build_config()` のメモリ上自動配線（`_apply_codd_gate_auto_wiring`）は削除済みで、
  `tests/test_agent_project.py::TestCoddGateNoAutoWiring` が「戻っていないこと」を固定している。
- `hooks.py` の `HOOK_CAPABILITIES`: 能力キー → 必須属性名の表（`wiring.detect` → `detect_wiring`、
  `wiring.findings` → `doctor_findings`）。設定 `hooks:` の明示指定 → sibling 走査、の順で
  プロバイダを引き当てる。
- `doctor.py::doctor_wiring_findings`: 引き当てたプロバイダを呼んで所見へ混ぜるだけ。

**パッケージ外（sibling）が持つもの** — codd-gate 固有の判断すべて。

| モジュール | 責務 |
|---|---|
| `codd_gate_detect` / `codd_gate_status` | 実バイナリへの問い合わせと、使えないときの no-op 縮退 |
| `codd_gate_routing` | `--repos` / `--repo-dir` の実引数組み立て |
| `codd_gate_wiring` | 実測配線（`probe_wiring`）・結線の有無判定・推奨文字列・所見整形（`render_findings`）＋読み取り専用 CLI |
| `codd_gate_regression` | 推奨文字列の生成と yaml への冪等 upsert（唯一の書き込み経路）＋ CLI |
| `codd_gate_base` / `codd_gate_debt` | base rev 解決 / intake 出力のレコード単位パース |

## 結線の入口は3つだけ

1. **yaml へ2行手書き**（正準）。`regression_cmd:`（done 前の差分ゲート）と `intake_cmd:`（負債の
   修復タスク化）。charter acceptance に `codd-gate verify --debt --max-broken N` を足せば
   受入の負債ラチェットも効く。
2. **`regression_cmd` の1行だけ CLI で注入**。`python3 codd_gate_regression.py --config
   .agent/agent-project.yaml`。codd-gate を実測し、使えるときだけ1キーを冪等 upsert する
   （コメントを壊さない行編集）。`--config` は**実在する設定ファイル**を要求する——無ければ
   `root:`/`agent_cli:` を欠いた起動不能な yaml を「パス誤りの成功」として作ってしまうため、
   エラーで止める。終了コードは 0=注入済み/冪等 no-op、1=設定ファイル不在・読めない、
   2=引数誤り、3=codd-gate が使えず何も書いていない。`intake_cmd` に対応する注入 CLI は無く、
   こちらは yaml 直編集のみ（非対称）。
3. **doctor に所見を出させる**。`.agent/agent-project.yaml` へ `hooks:` ＋ `  wiring: codd_gate_wiring`
   の2行を書いたときだけ到達する。

### 「明示しないと doctor が無所見」は仕様であって不具合ではない

`codd_gate_wiring.py` は契約名 `detect_wiring` / `doctor_findings` を `def` ではなくファイル末尾の
**別名**（`detect_wiring = probe_wiring` / `doctor_findings = render_findings`）として公開している。
本体の sibling 走査はソーステキストの `^def <属性名>(` を前置フィルタに使うため、この書き方だと
走査に載らない。零設定で当選してしまうと「パッケージ外に置いたのに設定なしで繋がる」という、
置き場と有効化手順の食い違いが戻る。`tests/test_codd_gate_wiring.py::TestHookResolution` が
両方向（明示すれば繋がる／走査では拾わない）を固定している。
`agent-project.yaml.example` にも走査の前置フィルタと、直下の唯一の例が自動検出されない旨を注記した。

### doctor 経路と CLI 経路で前提が1つ違う（loop が実測で足した差分）

doctor 経路は `charter.py::repo_registry_path` から repos.json のパスを受け取り、これは
**実在しなければ None を返す**。`judge_wiring` の `can_recommend = status.usable and repos_path is not None`
により、`hooks:` を書いても repos.json が無ければ所見は 0 件になる。

| 条件 | doctor の「未結線」所見 |
|---|---|
| `hooks:` なし | 0 件（repos.json の有無によらず） |
| `hooks:` あり・`repos.json` なし | 0 件 |
| `hooks:` あり・`repos.json` あり | 2 件 |

CLI 経路（`codd_gate_wiring.py --config …`）は `infer_default_repos_path` が `root:` から
パス文字列を組むだけで実在を問わないため、この前提に縛られない。本統合でも実測で確認した
（repos.json の無い一時プロジェクトで `usable:true` / `regression_wired:false` ＋推奨2件）。
設定を1行も足さずに現状を知る手段として README はこちらを先に置いている。

## 変更したファイル一覧

パッケージ外の sibling とドキュメントのみ。`agent_project/` パッケージ内・dashboard の差分は 0。

| ファイル | 変更の要点 |
|---|---|
| `tools/agent-project/codd_gate_wiring.py` | `detect_wiring`/`doctor_findings` を `probe_wiring`/`render_findings` へ改名し末尾で別名公開、読み取り専用 CLI（`main`）追加、docstring に「自分を結線しない」根拠を明記 |
| `tools/agent-project/codd_gate_regression.py` | `EXIT_*` 定数と `--help` の終了コード表、設定ファイル不在をエラーで止める挙動、docstring 更新 |
| `tools/agent-project/codd_gate_base.py` | docstring を現状（誰も自動では掴まない純粋関数）へ更新 |
| `tools/agent-project/README.md` | 一貫性ゲート節を全面改稿。有効化=yaml 2行を正準に、CLI 注入・終了コード・doctor 到達条件・repos.json 前提を追記 |
| `tools/agent-project/GUIDE.md` | 一貫性ゲート（opt-in）の位置づけ、doctor 節の到達条件、安全装置早見表・設定キー表への行追加。手順は複製せず README を正本と明示 |
| `tools/agent-project/agent-project.yaml.example` | `hooks:` の走査が `def <契約名>(` で前置フィルタする旨を注記 |
| `tools/agent-project/tests/test_codd_gate_{base,regression,routing,wiring}.py` | 上記を固定するテスト（計 111 件） |
| `docs/designs/codd-gate-design.md` | 設計正典の「現在地（結線状況）」を新境界へ更新（※スコープ注記は @followup 参照） |

## 完了条件コマンドの実行結果

すべて本統合で再実行し、依存タスクの報告を追認した。

| コマンド | 結果 |
|---|---|
| `python3 -m unittest discover -s . -t . -p 'test_codd_gate_*.py'`（`tools/agent-project/tests` で実行） | **rc=0**、111 tests OK |
| `grep -nE 'codd_gate_regression\|regression_cmd\|intake_cmd' README.md` | ヒットあり（276-284 / 291 / 447-453）＝ 期待どおり |
| `grep -nE 'build_config.*メモリ上で自動\|_apply_codd_gate_auto_wiring' README.md` | **0 hit** ＝ 期待どおり（旧・自動配線の記述が残っていない） |
| `python3 codd_gate_wiring.py --config <tmp>/agent-project.yaml` | rc=0、`usable:true` / `regression_wired:false` / 推奨2件 |
| `python3 codd_gate_regression.py --config <tmp>/agent-project.yaml --dry-run` | rc=0、`changed:true`・書き込みなし |
| `python3 codd_gate_regression.py --config <存在しないパス>` | **rc=1**（README 記載の終了コードと一致） |

テスト実行の注意: `-s tests` を作業ディレクトリ `tools/agent-project` から指定すると
`ImportError: Start directory is not importable`（`tests/__init__.py` が無い）で 0 件収集になる。
`tests/` へ入って `-s . -t .` で走らせるのが正しい呼び方。

## 依存タスクの結論に対する上積み・訂正

- loop の報告「gate の裏取りに前提が1段抜けていた（repos.json 実在が推奨組み立ての条件そのもの）」は
  本統合でも実測で再現し、正しいと確認した。README の記述もこの実測と一致している。
- 設計正典（`codd-gate-design.md`）が根拠として挙げる `TestCoddGateNoAutoWiring` と
  `HOOK_CAPABILITIES` / `doctor_wiring_findings` の実在を現物で確認した（それぞれ
  `tests/test_agent_project.py:3999`、`agent_project/hooks.py:15`、`agent_project/doctor.py:313`）。
  設計書の記述に実装との乖離は見つからなかった。
- loop が残した @followup（設計書のモジュール表へ `recommend_regression_cmd` を追記）は、t7 が
  同じ表の行を書き換えた後も**未反映のまま**（`grep` で 0 hit）。有効な指摘として引き継ぐ。

## スコープ外として手を出さなかった事項

- `agent_project/` パッケージ内への再結合、dashboard の変更 — 本 run の明示的な非スコープ。
- `intake_cmd` の注入 CLI 新設 — 現状は yaml 直編集のみという非対称を、ドキュメントで説明する側に倒した。
- `docs/designs/` 配下の追記 — 本統合ノードの書込許可は `tools/agent-project` 配下に限られる。

@followup `docs/designs/codd-gate-design.md` のモジュール表（`codd_gate_wiring.py` 行）へ公開名 `recommend_regression_cmd` / `recommend_intake_cmd` を追記する。CLI と doctor の双方から実際に使われる公開 API で、`codd_gate_regression.infer_default_repos_path` と対になる。現状 grep で 0 hit。
@followup スコープ規約の食い違いを解消する。本 run では t7 が `docs/designs/codd-gate-design.md`（`tools/agent-project` 配下ではない）を更新した一方、loop ノードは同じファイルへの追記を書込許可外として見送った。設計正典の更新をどのノードの責務にするか、run 単位で決めて指示に書き下すべき。
@followup `tools/agent-project/tests/` に `__init__.py` が無いため `-s tests` 指定の discover が 0 件収集で「OK」を返す。完了条件コマンドが空振りしても成功に見える形なので、`__init__.py` を置くか、CI/ドキュメント側で `tests/` へ cd する呼び方を正本として固定する。
@followup `intake_cmd` を注入する CLI（`codd_gate_regression.py` と対称の実装）の要否を判断する。現状の非対称は「片方だけ自動化されている」理由が利用者から見えにくい。

## 他ノードへ伝播すべき恒常的な規約

```json
{
  "constraints": [
    {
      "id": "codd-gate-no-auto-wiring",
      "rule": "agent_project パッケージ内に codd-gate 固有の名前・検出経路・自動配線を再導入しない。build_config は regression_cmd/intake_cmd を設定ファイル・CLI・既定のまま通す差し込み点に徹する。",
      "rationale": "検出レイヤの置き場（パッケージ外 sibling）と有効化手順（明示設定）を一致させ、利用者から『なぜ動くのか・どう止めるのか』が見える状態を保つため。",
      "enforced_by": "tools/agent-project/tests/test_agent_project.py::TestCoddGateNoAutoWiring"
    },
    {
      "id": "hook-contract-alias-only",
      "rule": "codd_gate_wiring.py は契約名 detect_wiring / doctor_findings を `def` で定義せず、ファイル末尾の別名代入としてのみ公開する。実体名は probe_wiring / render_findings。",
      "rationale": "本体の sibling 走査はソーステキストの `^def <属性名>(` を前置フィルタに使うため、`def` で書くと零設定で自動当選し、明示設定という有効化手順と食い違う。",
      "enforced_by": "tools/agent-project/tests/test_codd_gate_wiring.py::TestHookResolution"
    },
    {
      "id": "yaml-single-writer",
      "rule": ".agent/agent-project.yaml へ書き込んでよいのは、人が明示的に起動する codd_gate_regression.py だけ。それ以外の機械的経路から書き換えない。",
      "rationale": "同ファイルは agent_project/state.py の _HUMAN_OWNED_STATE_FILES に含まれ、状態 worktree の鏡合わせが『機械は書かない』前提に立っている。",
      "enforced_by": "agent_project/state.py の _HUMAN_OWNED_STATE_FILES"
    },
    {
      "id": "codd-gate-exit-codes",
      "rule": "codd_gate_regression.py の終了コードは 0=注入済み/冪等 no-op、1=設定ファイル不在・読めない、2=引数誤り（argparse）、3=codd-gate が使えず何も書いていない。値を変えるときは README・--help・設計正典を同時に更新する。",
      "rationale": "呼び出し側が『未導入だから飛ばす』と『パスを間違えている』を区別できる必要があり、argparse が占有する 2 との衝突も避ける。",
      "enforced_by": "tools/agent-project/tests/test_codd_gate_regression.py"
    },
    {
      "id": "config-must-exist",
      "rule": "設定注入 CLI は --config が実在しないとき新規ファイルを作らず、非 0 で停止する。",
      "rationale": "root:/agent_cli: を欠いた起動不能な yaml が『パス誤りの成功』として生まれるのを防ぐ。読み手が気づけない失敗を、その場のエラーに倒す。",
      "enforced_by": "tools/agent-project/tests/test_codd_gate_regression.py"
    },
    {
      "id": "doc-source-of-truth",
      "rule": "一貫性ゲートの有効化手順の正本は tools/agent-project/README.md。GUIDE.md と agent-project.yaml.example は位置づけ・注意点のみを書き、手順を複製しない。設計上の正典は docs/designs/codd-gate-design.md。",
      "rationale": "同じ手順が複数箇所にあると片方だけ古くなる。実際に本 run で修正した記述のうち複数が旧・自動配線前提のまま残っていた。",
      "enforced_by": "README の grep ゲート（肯定側 codd_gate_regression|regression_cmd|intake_cmd / 否定側 build_config.*メモリ上で自動|_apply_codd_gate_auto_wiring）"
    },
    {
      "id": "recommend-requires-repos-path",
      "rule": "推奨コマンド文字列を出す条件は status.usable かつ repos_path is not None。doctor 経路は charter.repo_registry_path 経由で repos.json が実在しないと None になるため所見 0 件になる。この非対称をドキュメントから落とさない。",
      "rationale": "『hooks: を書いたのに何も出ない』を不具合と誤認させないため。CLI 経路は --repos を自分で決められるので同じ前提に縛られない。",
      "enforced_by": "codd_gate_wiring.judge_wiring / charter.repo_registry_path"
    },
    {
      "id": "test-discovery-invocation",
      "rule": "codd_gate 系テストは tools/agent-project/tests へ cd して `python3 -m unittest discover -s . -t . -p 'test_codd_gate_*.py'` で走らせる（111 件）。",
      "rationale": "tests/ に __init__.py が無く、親から -s tests を指定すると ImportError または 0 件収集で偽の OK を返す。",
      "enforced_by": "手順（未自動化。@followup 参照）"
    }
  ]
}
```
