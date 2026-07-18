切り口: `regression_cmd`/`intake_cmd` を「単一書き手」で固定する境界として §4/§4.1 を書き直す — パッケージは口を出すだけで値を書かず、値を書く主体は人（yaml/CLI で有効化）か `codd_gate_regression.py`（yaml へ永続化）に限る。「起動時に自動配線する層」という現行フレーミングを「隣接する任意部品＋一意の書き手」へ置き換える。

---

# §4/§4.1 差し替え草案（drop-in）

> 本ファイルは設計書 `docs/designs/codd-gate-design.md` の §4 冒頭〜§4.1 を置き換える正典プローズの草案。行番号は付けない（散文は行ドリフトするため、節・小見出しで参照する）。§4 の表①〜③（差し込み点）と §4.1 末尾「差し込み点選択の妥当性（検証）」ブロックは**据え置き**（t1 でアライン確認済み）。置換対象は (i) §4 冒頭の境界宣言の補強、(ii) §4.1 見出し・リード、(iii) §4.1「現在地（結線状況）」段落の全面書換、(iv) `.agent/agent-project.yaml` 段落の再フレーム。

## 4. agent-project との結合点（オプション連携・プラグイン境界）

連携は一方向のオプション（codd-gate 単体でも §3 の全ステージが完結する）。agent-project 本体は
無改造で、結合はすべて **agent-project が公式に定義する外部 CLI の差し込み点**
（正典: [`agent-project-design.md`](agent-project-design.md) §4.1 フック契約カタログ、E1〜E6）
のうち **E1（verify/acceptance）・E2（regression_cmd）・E3（intake_cmd）** を使う。外せば元に戻る。

**プラグイン境界（規範）**——この節の全記述はこの一線に従属する:

- **パッケージ（`agent_project/*`）は口だけを提供する。** E1〜E6 の差し込み点を用意するのが責務で、
  パッケージ内から codd-gate を名指し・import・自動配線しない。`regression_cmd`/`intake_cmd`/acceptance の
  **値をパッケージが書くことはない**（空なら空のまま通過する＝連携なし）。
- **`codd_gate_*.py` は `tools/agent-project/` 直下の任意 sibling 部品。** 標準ライブラリのみ・
  `agent-project.py` 側の型（Config/Charter/Task）に依存せず・単体テスト付き。**欠落・削除しても
  パッケージは同一挙動**（パッケージがこれらに依存しないため、no-op 縮退ですらなく「そもそも通らない」）。
- **`regression_cmd`/`intake_cmd` には書き手が一人しかいない。** 値がフィールドへ入る経路は次の 2 つだけ:
  (i) **有効化** = 人／install 手順が **yaml か CLI に書く**、または
  (ii) **永続化** = `codd_gate_regression.py` が **yaml へ冪等注入する**。
  この 2 経路以外（起動時の Config 生成による自動配線など）で値が入ることはない。
  結合の実体は `schemas/` の共通データ契約と、この一意の書き手が汎用フックへ置く**文字列値**だけ。

| # | 差し込み点 | 差し込み | 拡張する機能／効き方 |
|---|-----------|---------|--------------------|
| ① | E2 `regression_cmd`（設定/CLI） | `codd-gate verify --base "$KIRO_BASE_REV" --repos <root>/repos.json` | **検証ゲート**の拡張。毎タスクの verify PASS 後・done 確定前に横断検査。NG なら done せず人へ |
| ② | E1 charter `## acceptance` | `codd-gate verify --debt --max-broken 0 …` | **プロジェクト受入判定**の拡張。evaluate のたび負債ラチェットを決定的に判定 |
| ③ | E3 `intake_cmd`（設定/CLI） | `codd-gate tasks --debt [--cohort]` | **backlog の自走**の拡張（pull 型供給）。watch の周期（intake_interval）で負債→修復タスクを**冪等取り込み**（決定的なタスク id が冪等キー）。正準ループが消化（ルーティング・検収・自律度は既存機構のまま）。手動は E4（`enqueue --json` / `inbox/`） |
| ④ | E1 タスクの `- verify:` | `codd-gate check …` | **done の根拠**。修復タスクの完了を状態アサーションで判定 |
| （補） | repos レジストリ（`schemas/repos.schema.json`） | agent-project が charter から `<root>/repos.json` を自動生成 → codd-gate は `--repos` で読む | レジストリの共用。codd-gate は charter を読まない（完全独立）。identity (url, path, base) は共通 |

`$KIRO_BASE_REV` は agent-project が verify / regression に渡す act 前 HEAD（実装済みの規約）を
そのまま使う。ワークスペース運用（別 repo clone 内での verify 実行）でも、タスク生成時に
`--repo-dir <name>=.` を焼き込むことで clone 内で自己完結する。

### 4.1 任意部品（`tools/agent-project/codd_gate_*.py`）— 値の組み立てと永続化

表①〜③の文字列（`codd-gate verify …` / `codd-gate tasks --debt` 等）は、原則として人が yaml/CLI に
手書きする（§4 の境界どおり、パッケージが埋めることはない）。その手書きを楽にするための**任意の
生成・判定部品**が `tools/agent-project/` 直下に **sibling として**置いてある（標準ライブラリのみ・
`agent-project.py` 側の型に依存しない・単体テスト付き・**パッケージからは import されない**）。
これらは「値を組み立てて yaml へ**永続化**する（`codd_gate_regression.py`）」ためのツールであって、
コマンド起動時にパッケージへ値を差し込む配線層ではない。責務は「実在するか」「使ってよいか」
「実引数はどう組むか」の 3 段に分かれる。

| モジュール | 責務 | 主な関数／型 |
|---|---|---|
| `codd_gate_detect.py` | codd-gate 実体の解決・生の検出値 | `resolve_codd_gate()`（`resolve_agent_flow` と対称：explicit→PATH→同梱パス `tools/codd-gate/codd-gate.py`）／`get_version()`／`check_repos_schema_compat()`／`detect_capabilities()`（`--help` の実プローブで verify/tasks/`--debt` の対応を判定） |
| `codd_gate_status.py` | 検出結果の no-op 縮退 | `CoddGateStatus`（`binary`/`version`/`findings`。`usable` は「実在し findings が空」・`command(*args)` は usable でなければ `None`）／`build_status()`（実在→バージョン→schema 互換の短絡順で判定。下限 `MIN_SUPPORTED_VERSION=(1,0,0)`）／`detect_status()` |
| `codd_gate_routing.py` | regression/intake/acceptance 共通の実引数組み立て | `resolve_repos_arg()`（vcwd 配下なら相対パス、外なら絶対パス）／`resolve_repo_dir_arg()`（`NAME=DIR`）／`build_routing_args()` |
| `codd_gate_base.py` | 差分ゲートの base rev 解決 | `resolve_base_rev()`（`$KIRO_BASE_REV`→charter の repo `base:`→`HEAD~1` の順。base 未注入で `--base ""` が失敗する穴を埋める） |
| `codd_gate_debt.py` | intake 出力の task スキーマ正規化（任意パーサ） | `parse_debt_output()` → `DebtParseResult(items, errors)`／`DriftItem(title, id, fields)`。object/array どちらの stdout も受理し、`title` 欠落など不備な 1 件だけを `errors` に隔離して残りは処理を続ける |
| `codd_gate_wiring.py` | 実測と判定（doctor 表示・生成の材料。**パッケージへは配線しない**） | `detect_wiring()`（実在→バージョン→schema→能力の短絡順で実測し `WiringJudgment` を返す。`codd_gate_regression.py` が生成可否の判断材料に使う）／`judge_wiring()`（純粋関数）／`recommend_regression_cmd()`/`recommend_intake_cmd()`（推奨値の文字列生成）／`regression_wired()`/`intake_wired()`（手書き文字列が既に codd-gate を指すかの正規表現判定＝doctor の「設定済みか」表示の材料）／`doctor_findings()`（doctor 出力用の所見。読み取り専用） |

**データ契約**: 入力は `schemas/repos.schema.json` 準拠の `<root>/repos.json`（`check_repos_schema_compat`
がトップレベル object・`_` 接頭辞以外の値が object という最小構造を検査）。出力は
`schemas/task.schema.json` 準拠の `codd-gate tasks --debt` の stdout JSON で、**汎用 intake がこの JSON を
そのまま冪等取り込みする**（冪等キー＝決定的 id）。codd-gate 側の生 stdout を同スキーマへ正規化する
必要があれば `codd_gate_debt.py` が担う。`CoddGateStatus` はディスクにも `schemas/` にも乗らない
プロセス内一過性の値オブジェクトで、①未検出 ②バージョン不明 ③下限未満 ④repos.json 非互換の
いずれかで `usable=False` に倒れ、`command(*args)` が `None` を返す——これは**生成ツール側**が
「使えない環境では値を書かない」を 1 行で担保するための道具立てで、パッケージの通過判定には関与しない。

**有効化（値をどう入れるか）**: `regression_cmd`/`intake_cmd`/acceptance に codd-gate を効かせるには、
その値を **yaml か CLI に書く**。書き手は人か install 手順で、パッケージの Config 生成は該当フィールドを
**自動で埋めない**——手で書いていなければ空のまま（＝連携なし）で通過する。ゆえに「有効化されているか」は
yaml/CLI を読めば一意に決まり、起動時の環境プローブ結果に依存しない（決定的で、監査可能）。

**永続化（値をどこへ残すか）**: 手書きの代わりに値を生成して yaml へ**書き込む**唯一の主体は
`codd_gate_regression.py`。`python3 tools/agent-project/codd_gate_regression.py --config .agent/agent-project.yaml`
を人・install 手順が能動的に実行し、`build_regression_cmd()`（`codd_gate_detect`/`_status`/`_routing` で
実在・使用可否・実引数を解決）→ `upsert_config_text()`（正規表現ベースの冪等 upsert。PyYAML の
load→dump は使わず既存コメントを保持）→ `apply_to_file()` で `regression_cmd` を 1 行だけ注入する。
これが値のディスク永続化の唯一経路であり、書いた後は以後の全コマンドで同一に効く。表①〜③どおりの
手書きに代わる**能動的な生成ツール**という位置づけで、コマンド起動のたびに走る自動配線は**存在しない**。

**`.agent/agent-project.yaml` を機械が勝手に書き換えない**: 同ファイルは `agent_project/state.py` の
`_HUMAN_OWNED_STATE_FILES`（状態 worktree の鏡合わせが「機械は絶対に書かない」前提に立つ人専有ファイル
一覧）に含まれる。したがって yaml への書き込みは、人の手か、人が明示起動した `codd_gate_regression.py`
——どちらも「人が意図した書き込み」——に限られる。書き手が一意なので、鏡合わせは人の編集と機械の
書き込みを取り違えない。逆に言えば、build_config 等が起動ごとに値を差し込む設計は、この専有前提を
崩すため採らない。

**任意部品の可搬性（欠落時の挙動）**: `codd_gate_*.py` が隣に**無い／削除された**場合でも、パッケージは
同一に動く——パッケージは `codd_gate_*` を import せず、`regression_cmd`/`intake_cmd` が空なら単に連携が
無効なだけ（従来どおりの verify/intake）。さらに codd-gate バイナリ自体が未検出のときは、生成ツール
（`codd_gate_regression.py`）が `CoddGateStatus.usable=False` を見て**値を書かない**ので、「未検出環境に
手書き値を持ち込んでコマンド不在で block」という従来の落とし穴は、生成経由なら「最初から書かれない」に
置き換わる（手書き値をそのまま持ち込むかは人の判断。E2 が「止める」安全ゲート・E3 が「足す」任意機能
という非対称設計自体は変わらない）。

**差し込み点選択の妥当性（検証）**: 〔以降のブロックは据え置き（変更なし）〕

---

# 変更点マッピング（t1 ギャップ → 本草案の対応）

| ギャップ | 対応 |
|---|---|
| **G1**（§4.1「現在地」が build_config→自動配線を正典化） | 「現在地（結線状況）」段落を削除し、「有効化／永続化」段落へ全面書換。build_config 自動配線の記述を除去し、書き手を人（yaml/CLI）＋`codd_gate_regression.py` の 2 経路に一意化。受入 `! git grep _apply_codd_gate` が要求する関数消滅と設計記述を一致させる（コード側除去は §スコープ外＝@followup）。 |
| **G3**（見出し「自動検出レイヤ」＋「機械化する補助モジュール」のフレーミング） | 見出しを「任意部品（`codd_gate_*.py`）— 値の組み立てと永続化」へ変更。リードを「隣接する任意 sibling／パッケージからは import されない生成・判定部品」の語彙へ。 |
| **G4**（境界が規範として未明文化） | §4 冒頭に「プラグイン境界（規範）」の 3 条を追加（口だけ提供／任意 sibling／単一書き手）。§1 不変条件への1条追記が望ましいが §1 は本タスクのスコープ外（→ @followup）。 |
| **G5**（欠落時のパッケージ挙動が未保証） | 「任意部品の可搬性（欠落時の挙動）」段落を新設。`codd_gate_*` 欠落でもパッケージは同一挙動（依存しない）と明記。 |
| **G6**（汎用フックと専用配線の地続き混在） | §4 を「汎用の口（表①〜④）」、§4.1 を「任意部品の生成・永続化」に役割分離。専用の自動配線という層を設計から落とす。 |
| **G2**（README 一貫性ゲート節の自己矛盾） | README はスコープ外（本タスクは設計書 §4/§4.1）。設計と同じ一本化（有効化=設定のみ／永続化=`codd_gate_regression.py` のみ）を README へ伝播する必要（→ @followup）。 |
