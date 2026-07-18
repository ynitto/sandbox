# codd-gate — 設計書

> 最終更新: 2026-07-15 ／ 関連: `tools/codd-gate/`（`codd-gate.py` / `README.md` / `tests/`）,
> `tools/agent-project/`（`codd_gate_detect.py` 等の任意 sibling 部品含む）, `docs/designs/agent-project-design.md`
>
> 本書は codd-gate の**唯一の設計正典**。実装と差が出たら本書を更新する。

`codd-gate` は、**ドキュメント・コード・テストの一貫性を「受け入れ前のゲート」と「負債の棚卸し→
タスク化」で常時維持する**決定的ツール。[CoDD (Coherence-Driven Development)](https://github.com/yohey-w/codd-dev)
の設計（Trace / Impact / Verify、no fake green）の翻案。

**位置づけ（依存の向きと常駐の所在）**: codd-gate は **agent-project に依存しない独立ツール**
（依存は python3 と git のみ）。単体で CI・git hook・手元の点検に使え、独立したインストーラ
（`tools/codd-gate/install.sh`）と使い方ドキュメント（`tools/codd-gate/README.md`）を持つ。
agent-project との関係は**完全独立**（charter.md を読まない・双方が相手の実装を知らない）:
結合は `schemas/` の共通データ契約（repos / task）と、agent-project 側の**汎用**フック
（regression_cmd / intake_cmd / acceptance。§4）だけ。リポジトリレジストリは agent-project が
charter から `<root>/repos.json` を自動生成して**ファイルとして渡す**（codd-gate は `--repos` で
読むだけ）。導線として agent-project の install.sh は隣に codd-gate があれば同梱インストールし、
**有効化は設定だけ**で完了する。

**役割分担の固定（長期実行の禁止）**: **常駐（長期実行）は agent-project 側だけが持ち、codd-gate の
サブコマンドはすべて単発・有界**（watch/daemon を持たない。git 呼び出しも個別タイムアウト）。
「一つのサブコマンドが長期タスクにならない」を両ツールの境界として固定する——だから「常に一貫性をとる」の
繰り返しは intake_cmd / regression_cmd / acceptance（連携時）か cron・git hook・CI（単体時）が担い、
codd-gate は呼ばれるたびに 1 パスで判定して終わる。

**設計の出発点（要求）**:

1. ドキュメント・コード・テストの一貫性を**常に**とる（変更のたび・プロジェクトの受入時の両方）
2. agent-project に**プラグインのような形**で取り込める（本体のコードに手を入れない）。
   かつ単体でも完結して使える
3. **ブラウンフィールド**が主戦場 — ドキュメント・コード・テストは一部だけ存在し、それを整合性を
   取りつつ改修していく。要件は機能要件ではなく、**機能追加・リファクタリング・リアーキテクチャの
   指針（charter）**として与えられる
4. 成果物を格納するリポジトリは**複数**あり、**パス＋ブランチで一意**に決まる

---

## 1. 全体像

```
   charter.md（指針。## repos は互換入力）
        │ agent-project が repos ファイルへ自動生成（無ければ。_meta 付き・正は charter）
        ▼
   <root>/repos.json ←────────── schemas/repos.schema.json（共通データ契約）
        │ --repos で読む（codd-gate は charter を知らない）
      ┌─┴───────────────────────────────────────────────────────────────┐
      │ agent-project（無改造）                                        │
      │   plan → execute（act=agent-flow）→ verify ゲート → done          │
      │                          │verify/regression に $KIRO_BASE_REV    │
      │        ┌─────────────────▼─────────────┐                        │
      │        │ ① codd-gate verify --base …   │ ← 差分ゲート（毎タスク） │
      │        └───────────────────────────────┘                        │
      │   acceptance（プロジェクト受入）                                  │
      │        ┌───────────────────────────────┐                        │
      │        │ ② codd-gate verify --debt …   │ ← 負債ラチェット         │
      │        └───────────────────────────────┘                        │
      │   backlog ◀── ③ codd-gate tasks ＝ schemas/task.schema.json      │
      │              （intake_cmd / enqueue --json / inbox が読む）       │
      └─────────────────────────────────────────────────────────────────┘
                        ▲
      scan / impact / verify / tasks / check（本体・決定的・stdlib のみ）
                        │
      ┌── repo A（url, path, base）──┐  ┌── repo B ──┐  …  ← --repo-dir で checkout を対応付け
      │ docs/ ── code ── tests      │  │ docs/ …    │
      └─────────────────────────────┘  └────────────┘
```

| CoDD | codd-gate | 実体 |
|------|-----------|------|
| Trace（接続マップ） | `scan` | doc↔code↔test のエッジ＋負債（壊れた参照/未文書化/未テスト）の棚卸し |
| Impact（Green/Amber/Gray） | `impact` | 差分の分類。**Followup**（別 repo への追随）を追加 |
| Verify（no fake green） | `verify` | 毎回フレッシュにスキャンして差分と突合。exit 0/1 |
| Fix（伝搬） | `tasks` | 修復タスクを生成し **実行は agent-project → agent-flow に委譲** |
| —（CoDD に無い） | `check` | 修復タスクの verify 用の状態アサーション（接続・参照解決・鮮度） |

### 不変条件（agent-project の 5 か条に従属する）

1. **判定は「現在の状態と差分」だけから決める。** マップファイルはキャッシュ/可視化用であり、
   verify は毎回スキャンし直す（stale なマップで偽 PASS を作らない＝no fake green）。
2. **成果の無い場所で偽判定しない。** チェック対象 repo のローカル checkout が解決できなければ
   exit 2 で止まる。未解決 repo は「未スキャン」として明示され、黙って PASS 側に倒れない。
3. **ブラウンフィールドの既存負債で止めない。** 差分ゲートは「この変更が新しく壊した/置き去りに
   した分」だけを NG にする。既存負債は `--debt` の棚卸しとラチェット（`--max-*`）で漸進的に返す。
4. **決定的・stdlib のみ・LLM 不要。** 接続の推定は注釈＞構文（バッククォート/リンク/import/命名規約）
   の固定規則。修復の知能（何をどう書き直すか）は agent-project → agent-flow へ委譲する。
5. **安全ゲートは「足す/止める」方向のみ。** codd-gate が done を作ることはない（agent-project の
   「done は verify の exit 0 のみ」の鉄則に、NG 側の条件として上乗せされるだけ）。
6. **どのサブコマンドも単発・有界。** watch/daemon を持たず、必ず 1 パスで終了する（git 呼び出しも
   個別タイムアウト）。常駐・繰り返しは agent-project（intake_cmd / regression_cmd / acceptance）や
   cron・git hook・CI の側に置く。

---

## 2. データモデル

### ノード＝成果物、識別子は「repo 名 : repo 相対パス」

リポジトリの identity は **(url, path, base)**＝「パス＋ブランチで一意」。レジストリの形式は
**ツール横断の共通スキーマ [`schemas/repos.schema.json`](../../schemas/repos.schema.json)**
（agent-project の `<root>/repos.yaml`・agent-flow の `--workspace` 射影と共通。正典は
`schemas/README.md`）で、エントリ名がノード識別子のプレフィックスになる（モノレポは path 別
エントリ、ブランチ別は base 別エントリで、それぞれ**別 repo 名＝別ノード空間**になる）。解決順:
`--repos` / 設定 `repos_file`（共通スキーマの独立ファイル）＞ 設定 `repos:`（インライン・同形）＞
`--repo-dir` の名前 ＞ cwd 単一 repo。charter.md は**読まない**（agent-project 側が charter から
repos.json を自動生成して渡す）。
ローカル checkout は常に CLI `--repo-dir` が設定より勝つ。

```
ノード:  app:src/util.py            kind ∈ {doc, code, test}（other は対象外）
エッジ:  {src, dst, kind ∈ {documents, tests}, evidence="docs/x.md:12 (inline)"}
負債:    broken_refs（解決できない参照）/ orphans.undocumented / orphans.untested
```

### 分類（kind）— repo ごとに上書き可能

`doc（拡張子 .md/.rst/.adoc または docs グロブ） > test（tests グロブ・test_*/․test.* 規約） >
code（code グロブ、未指定なら拡張子表）> other` の優先順。グロブは repos レジストリの
`docs:/tests:/code:`（共通スキーマの拡張キー）で repo ごとに上書きする。agent-project 経由なら
charter の repo エントリに同名キーを書けば repos.json への自動生成で損失なく引き継がれる。

### 接続の推定（決定的・優先順）

1. **明示注釈**（最優先・全ファイル種別・コードフェンス内でも有効）:
   `coherence: doc=<[repo:]path>` / `code=…` / `test=…`（カンマ区切り複数可）
2. doc: インラインコード `` `path` `` と md リンク `[x](path)`（フェンス内は無視）
3. test: Python import（`import a.b` / `from a.b import c` → `a/b.py` 等へ一意解決時のみ）、
   `/` を含む文字列リテラル、命名規約 `test_x ↔ x`（同一 repo で stem 一意のときのみ）
4. リポジトリ横断は `repo名:相対パス` の明示プレフィックス、または素のパスが他 repo で一意に
   解決する場合

**曖昧は接続しない・負債にもしない**（同名複数・単語トークン等）。`/` を含む参照が
どこにも解決しないときだけ broken_refs（壊れた参照＝負債）とする。誤検出を嫌う側に倒し、
足りない接続は注釈で人が宣言する（ブラウンフィールドで漸進的に地図を濃くする想定）。

---

## 3. 処理フロー（ステージ別）

### scan — Trace

```
repos 解決（--repos / 設定 repos: / --repo-dir） → 各 repo の git ls-files（＋未追跡、
作業ツリーに実在するものだけ） → kind 分類 → 参照抽出 → 解決 → ノード/エッジ/負債
→ map.json（可視化・棚卸し用。判定のキャッシュにはしない）
```

- index に残っていても作業ツリーから消えたファイルは「実在しない」— 削除の追随漏れを
  参照切れとして検出するための规約。
- 各 repo の HEAD と現在ブランチを記録する（監査用。**ブランチ不一致を NG にはしない** —
  agent-flow は base から作業ブランチ `kf/<run-id>` を切って作業するため、検証時点のブランチは
  base と一致しないのが正常）。

### impact / verify — Impact + no fake green

```
差分（--base|$KIRO_BASE_REV .. 作業ツリー、staged/unstaged/未追跡込み）
  → 変更ファイルごとに分類:
     code 変更:  接続 doc が同一差分で更新済み → GREEN ／ 同一 repo で未更新 → AMBER(doc-stale)
                 接続 doc が別 repo → FOLLOWUP ／ 接続ゼロ → GRAY(unmapped)
     doc/test 変更: 参照が全て解決 → GREEN ／ 解決しない参照 → AMBER(broken-ref)
     削除:       未更新の doc/test が削除先を参照したまま → AMBER(dangling-ref)
  → verify: AMBER>0（--strict なら GRAY も、--strict-cross なら FOLLOWUP も）→ exit 1
```

- **テスト未更新は Amber にしない**。コード変更でテストが変わらないのは正常で、テストが通るか
  どうかは agent-project の verify / regression の領分。テスト接続は未テスト負債と削除追随に使う。
- **Followup を既定 PASS にする理由**: agent-project では 1 タスク＝1 リポジトリ書込
  （ワークスペース・ルーティング）なので、別 repo のドキュメント追随は同じ差分内では原理的に
  完了できない。NG で止める代わりに `tasks` が追随タスクを生成して backlog に返す
  （「足す」方向の安全ゲート）。

### verify --debt — 負債ラチェット

差分ではなく全体棚卸し（broken_refs / undocumented / untested の件数）を `--max-*` と突合する。
しきい値未指定は報告のみ（exit 0）。charter acceptance に置いて数値を段階的に下げることで、
「整合性を取りつつ改修していく」がプロジェクトの done 条件になる。

### tasks — Fix（共通スキーマ出力。実行は委譲）

**責務境界（タスク追加のインターフェース）**: agent-project は**元よりタスクを入力とする設計**
（enqueue の実装注釈に「汎用の取り込み口——外部ソースは薄いアダプタでここへ流し込む」、書式の正典は
`backlog.md.example`、spec の未知キーは保持＝前方互換の緩い契約）。その JSON 表現は
**共通 task スキーマ（`schemas/task.schema.json`）**として独立管理される。よって
- **codd-gate コアの正は所見（findings）**: `impact --json`（green/amber/gray/followup の
  {type, node, counterpart, detail}）と `verify --debt --json` / `scan`（broken_refs / orphans）。
- **`tasks` は所見を共通 task スキーマへ直接出力する**——特定ツール向け「アダプタ」ではない
  （スキーマを読める消化先なら agent-project でも他のタスクランナーでもよい）。workspace /
  paths / cohort_items / expect は共通スキーマのフィールドであり、codd-gate は agent-project の
  実装を知らずにこれらを埋められる。スキーマ外の消化先（issue tracker 等）へは所見 JSON から変換する。
- この構造により **codd-gate は agent-project から完全に独立**（入力: repos スキーマ、出力:
  task スキーマ、どちらも `schemas/` の共通契約）。

| 発見 | 生成タスク | done の根拠（agent-project の鉄則に整合） |
|------|-----------|--------------------------------------------|
| doc-stale（同一 repo） | 「X の変更を doc Y へ反映」 | `check --doc Y --code X --fresh`（状態: 接続・参照解決・鮮度）＋ `expect: changes` |
| broken-ref / dangling-ref | 「Y の壊れた参照を修正」 | `check --refs Y` |
| doc-stale-cross（別 repo） | 「X の変更を repo B の doc Y へ反映」 | `- accept:`（自然言語→agent-project が verify 合成 or 人へ）＋ `- workspace: B` ＋ `- paths:` |
| unmapped / undocumented | 「X を文書化/接続」 | `check --covered X --need doc` |
| untested | 「X のテストを追加」 | `check --covered X --need test` |

出力は enqueue --json 互換（`id`/`title`/`verify`/`accept`/`priority`/`paths`/`workspace`/`note`/
`expect`。未知キーも agent-project 側で保持される）。`--inbox DIR` で 1 タスク 1 JSON の
ファイル投入もできる（verify を持つので triage で ready に昇格する）。

**後段のタスク分解を前提にした粒度**: 常に **1 発見 = 1 タスク**（小さく・個別に verify 可能）で出し、
「全部直す」型の長期タスクは生成しない。未文書化・未テストのような**同種作業の山**は
`tasks --debt --cohort` で repo 単位の cohort spec（`cohort_items`＋`{item}` プレースホルダ）にまとめ、
agent-project の pilot-then-batch（1 件を人の検収で固めてから残りを自動展開）へ分解を委ねる。
タスク `id` は発見内容から決定的に生成（agent-project の id 規約 [A-Za-z0-9_-]・48 字に収め、
末尾ハッシュで切り詰め衝突を回避）——intake_cmd の冪等キーとして機能する。

### check — 修復タスクの verify 用アサーション

「履歴でなく望む最終状態を見る」の具体化。エッジ存在・参照解決・**鮮度**
（doc の実質最終変更 ≥ code のそれ。未コミット変更は「今」とみなす）を状態としてアサートする。
`git log | grep` 型の履歴 verify を書かせないための部品。

### git アクセスの原則（リモート負荷と鮮度）

1. **通常動作はローカル読み取りのみ**。git 操作は `ls-files` / `diff --name-status` / `rev-parse` /
   `status --porcelain` / `log -1` に限られ、clone / fetch は一切しない（ネットワーク非依存・
   **フル clone はどの経路にも存在しない**）。
2. **`--sync`（opt-in）**: `dir` 未解決かつ `url` を持つ repo だけを、
   [`git-worktree-cache-pattern.md`](git-worktree-cache-pattern.md) 準拠で実体化する——
   共有 bare ミラー（初回のみ `--mirror --filter=blob:none`・以後は増分 fetch。root は
   `KIRO_GIT_CACHE_DIR`＝agent-flow / agent-project と共有）→ **fetch 後の SHA** から
   detached worktree（INV-1 鮮度）。URL ロック・`gc.auto=0`・破損時 nuke&re-mirror（INV-2）、
   全滅時は浅 clone `--depth 1` へフォールバック（INV-3）。run 後に worktree だけ回収し
   ミラーは残す（次回は増分のみ＝リモートの pack 生成負荷を「初回＋増分」に圧縮）。
   実体化できない repo は未解決のまま＝**黙って PASS 側に倒さない**（不変条件 2）。
3. **`dir` 解決済みの repo には fetch も clone もしない**。差分ゲートの判定対象は
   **作業ツリーそのもの**（いま手元にある変更）であり、鮮度の主語が違う——リモートの最新
   base 起点で負債を測りたい参照 repo は url＋`--sync` で与えるのが正しい使い分け。

---

## 4. agent-project との結合点（オプション連携・プラグイン境界）

連携は一方向のオプション（codd-gate 単体でも §3 の全ステージが完結する）。agent-project 本体は
無改造で、結合はすべて **agent-project が公式に定義する外部 CLI の差し込み点**
（正典: [`agent-project-design.md`](agent-project-design.md) §4.1 フック契約カタログ、E1〜E6）
のうち **E1（verify/acceptance）・E2（regression_cmd）・E3（intake_cmd）** を使う。外せば元に戻る。

**プラグイン境界（規範）**。この節の記述はすべてこの一線に従う。

- **パッケージ（`agent_project/*`）は汎用フックだけを提供する。** E1〜E6 の差し込み点を用意するのが責務で、
  パッケージのコードから codd-gate を名指し・import・自動配線しない。`regression_cmd`/`intake_cmd`/acceptance の
  値をパッケージが書くことはない（手で書いていなければ空のまま通過する＝連携なし）。パッケージにとって
  codd-gate は「汎用フックへ渡されうる任意の値」であって、コード上の依存先ではない。
- **`codd_gate_*.py` は `tools/agent-project/` 直下の任意 sibling 部品。** 標準ライブラリだけで動き、
  `agent-project.py` 側の型（Config/Charter/Task）に依存せず、単体テストを持ち、パッケージからは import されない。
  パッケージ・ディレクトリ `agent_project/` の外（その隣）に置くので、`agent_project.codd_gate_*` ではなく
  トップレベルの sibling モジュールとしてだけ存在する。丸ごと削除してもパッケージは同一挙動を保つ。
- **`regression_cmd`/`intake_cmd` に値を書く主体は一つだけ。** 値がフィールドへ入る経路は 2 つに限る。
  (i) 有効化＝人か install 手順が yaml か CLI に書く。(ii) 永続化＝`codd_gate_regression.py` が yaml へ冪等注入する。
  起動時の Config 生成（`build_config`）が値を差し込む経路は設けない。結合の実体は `schemas/` の共通データ契約と、
  この一意の書き手が汎用フックへ置く文字列値だけ。

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

### 4.1 値の組み立てと永続化を担う任意部品（`tools/agent-project/codd_gate_*.py`）

表①〜③の文字列（`codd-gate verify …` や `codd-gate tasks --debt` 等）は、原則として人が yaml か CLI に手で書く
（§4 の境界どおり、パッケージが埋めることはない）。その手書きを楽にするための任意の生成・判定部品が、
`tools/agent-project/` 直下に **sibling として**置いてある（標準ライブラリのみ・agent-project.py 側の型
（Config/Charter/Task）に依存しない・単体テスト付き・パッケージからは import されない）。これらは値を組み立てて
yaml へ**永続化**する（`codd_gate_regression.py`）ための道具であって、コマンド起動時にパッケージへ値を差し込む
配線層ではない。責務は「実在するか」「使ってよいか」「実引数はどう組むか」の3段に分かれる。

| モジュール | 責務 | 主な関数／型 |
|---|---|---|
| `codd_gate_detect.py` | codd-gate 実体の解決・生の検出値 | `resolve_codd_gate()`（`resolve_agent_flow` と対称：explicit→PATH→同梱パス `tools/codd-gate/codd-gate.py`）／`get_version()`／`check_repos_schema_compat()`／`detect_capabilities()`（`--help` の実プローブで verify/tasks/`--debt` の対応を判定） |
| `codd_gate_status.py` | 検出結果の no-op 縮退 | `CoddGateStatus`（`binary`/`version`/`findings`。`usable` は「実在し findings が空」・`command(*args)` は usable でなければ `None`）／`build_status()`（実在→バージョン→schema 互換の短絡順で判定。下限 `MIN_SUPPORTED_VERSION=(1,0,0)`）／`detect_status()` |
| `codd_gate_routing.py` | regression/intake/acceptance 共通の実引数組み立て | `resolve_repos_arg()`（vcwd 配下なら相対パス、外なら絶対パス）／`resolve_repo_dir_arg()`（`NAME=DIR`）／`build_routing_args()` |
| `codd_gate_base.py` | 差分ゲートの base rev 解決 | `resolve_base_rev()`（`$KIRO_BASE_REV`→charter の repo `base:`→`HEAD~1` の順。base 未注入で `--base ""` が失敗する穴を埋める） |
| `codd_gate_debt.py` | intake 出力の task スキーマ正規化（任意パーサ） | `parse_debt_output()` → `DebtParseResult(items, errors)`／`DriftItem(title, id, fields)`。object/array どちらの stdout も受理し、`title` 欠落など不備な1件だけを `errors` に隔離して残りは処理を続ける |
| `codd_gate_wiring.py` | 実測と判定（doctor 表示・生成の材料。**パッケージへは配線しない**） | `detect_wiring()`（実在→バージョン→schema→能力の短絡順で実測し `WiringJudgment` を返す。`codd_gate_regression.py` が生成可否の判断に使う）／`judge_wiring()`（純粋関数）／`recommend_regression_cmd()`／`recommend_intake_cmd()`（推奨値の文字列生成）／`regression_wired()`／`intake_wired()`（手書き文字列が既に codd-gate を指すかの正規表現判定＝doctor の表示材料）／`doctor_findings()`（doctor 出力用の所見。読み取り専用） |
| `codd_gate_regression.py` | regression_cmd の生成・yaml への冪等注入（CLI） | `build_regression_cmd()`／`upsert_config_text()`（正規表現ベースの最小差分行編集。PyYAML load→dump は使わず既存コメントを保持）／`apply_to_file()`。`python3 codd_gate_regression.py --config .agent/agent-project.yaml` で人・install 手順が明示的に実行する生成ツール |

**データ契約**: 入力は `schemas/repos.schema.json` 準拠の `<root>/repos.json`（`check_repos_schema_compat`
がトップレベル object・`_` 接頭辞以外の値が object という最小構造を検査）。出力は
`schemas/task.schema.json` 準拠の `codd-gate tasks --debt` の stdout JSON（`parse_debt_output` が
受理・正規化し、`DriftItem.to_spec()` で `enqueue_task` / `run_intake` がそのまま飲める dict に戻る）。
`CoddGateStatus` はディスクにも `schemas/` にも乗らないプロセス内一過性の値オブジェクトで、
①未検出 ②バージョン不明 ③下限未満 ④repos.json 非互換のいずれかで `usable=False` に倒れる
（生成ツールは `if status.command(...):` の1行で「使えない環境では値を書かず既存挙動のまま通過する」
を担保できる設計）。

**有効化（値をどう入れるか）**: `regression_cmd`/`intake_cmd`/acceptance に codd-gate を効かせるには、
その値を yaml か CLI に書く。書き手は人か install 手順で、パッケージの Config 生成は該当フィールドを
自動で埋めない。手で書いていなければ空のまま（＝連携なし）で通過する。だから「有効化されているか」は
yaml/CLI を読めば一意に決まり、起動時の環境プローブ結果には依存しない（決定的で監査できる）。

**永続化（値をどこへ残すか）**: 手書きの代わりに値を生成して yaml へ書き込む主体は
`codd_gate_regression.py` 一つだけ。人か install 手順が
`python3 tools/agent-project/codd_gate_regression.py --config .agent/agent-project.yaml` を能動的に実行し、
`build_regression_cmd()`（`codd_gate_detect`/`_status`/`_routing` で実在・使用可否・実引数を解決）→
`upsert_config_text()`（正規表現ベースの冪等 upsert。PyYAML の load→dump は使わず既存コメントを残す）→
`apply_to_file()` で `regression_cmd` を1行だけ注入する。これが値をディスクへ残す唯一の経路で、書いた後は
以後の全コマンドで同一に効く。コマンド起動のたびに走る自動配線は無い。人が明示した値は独立キー単位で
常に優先され、codd-gate 未検出・バージョン不適合・schema 不適合・capability 不足のいずれでも生成ツールが
`CoddGateStatus.usable=False` を見て値を書かない。

**`.agent/agent-project.yaml` を機械が勝手に書き換えない**: 同ファイルは `agent_project/state.py` の
`_HUMAN_OWNED_STATE_FILES`（状態 worktree の鏡合わせが「機械は書かない」前提に立つ人専有ファイル一覧）に
含まれる。だから yaml への書き込みは、人の手か、人が明示起動した `codd_gate_regression.py` に限られる。
どちらも人が意図した書き込みで、書き手が一つに定まるので、鏡合わせは人の編集と機械の書き込みを取り違えない。
起動のたびに Config 生成が値を差し込む設計は、この専有前提を崩すため採らない。

**任意部品の可搬性（欠落時の挙動）**: `codd_gate_*.py` が隣に無い、または削除された場合でも、パッケージは
同一に動く。パッケージは `codd_gate_*` を import せず、`regression_cmd`/`intake_cmd` が空なら連携が無効なだけ
（従来どおりの verify/intake）。さらに codd-gate バイナリ自体が未検出のときは、生成ツール
（`codd_gate_regression.py`）が `CoddGateStatus.usable=False` を見て値を書かないので、「未検出環境に手書き値を
持ち込んでコマンド不在で block される」という従来の落とし穴は、生成経由なら「最初から書かれない」に置き換わる
（手書き値をそのまま持ち込むかは人の判断。E2＝止める安全ゲート、E3＝足す任意機能という非対称は変わらない）。

**差し込み点選択の妥当性（検証）**:

- **差分ゲートを E1（各タスクの verify）でなく E2（regression_cmd）に置く理由**: E1 はタスク作者が書く
  「そのタスク固有の完了条件」で、書き忘れ・書き換えができてしまう。一貫性は**全タスクに例外なく**
  課したい横断検査なので、タスク非依存に常時上乗せされる E2 が設計意図どおりの位置（E2 はまさに
  「巻き込み事故の検知」のための口）。タスク固有の追加条件として E1 に併記するのは自由。
- **負債の返済を cron＋E4（inbox）でなく E3（intake_cmd）に置く理由**: cron 案はスケジューラが 2 つに
  なり（ループの idle/予算と無関係に発火）、重複投入の冪等性も自前実装になる。E3 はループの周期・idle と
  一体で動き、冪等キー（id）・間隔律速・失敗無害化を口の側が保証する。イベント駆動の外部ソース
  （webhook 等）は引き続き E4 が適位置——「周期 pull は E3・イベント push は E4」の使い分け。
- **負債ラチェットを E1（acceptance）に置く理由**: プロジェクト done の唯一の根拠は acceptance 全 PASS
  （agent-project の不変条件）。「整合性を取りつつ改修していく」をプロジェクトの完了条件にするには、
  その正位置に置くしかない。
- **E5（notify_cmd）・E6（executor）は使わない**: codd-gate は通知も実行も持たない（分類とタスク生成
  まで）。修復の実行は正準ループ→agent-flow の領分。
- **agent-flow（実行層）には差し込まない**: agent-flow が公式に持つプラグイン機構は executor（E6 相当・
  「どう実行するか」の差し替え口）のみで、exit code を契約とする決定的ゲートの差し込み口は無い。
  agent-flow 内の verify / gate ノードは**エージェントによる内側の品質ループ**（敵対的レビュー等）で
  あり、決定的な合否とは別物。これは 3 層の責務分担どおり——**決定的な合否（done の根拠）は制御層
  agent-project の専管**で、agent-flow の act の成果は必ず外側の E1/E2 ゲートを通ってから done に
  なるため、内側に同じゲートを重ねると責務の一元性が崩れる。agent-flow を単体で使うときはフック不要の
  シェル合成で足りる（`agent-flow run "…" && codd-gate verify --base …`）。将来、内側の決定的ゲートが
  本当に必要になれば、agent-flow に「静止後・final 確定前に走る `gate_cmd`」を E2 の相似形（単発・
  有界・exit code 契約）として追加する道はあるが、現状は外側で必ずゲートされるため設けない。

### 4.2 境界の完了条件（決定的ゲート）

§4 の境界、すなわち「パッケージは汎用フックだけを提供し codd-gate を名指し・import・自動配線しない／
codd_gate_* は任意の sibling 部品」を、散文の宣言ではなく機械可読な受入で固定する。設計上の完了条件は、
次の1行が exit 0 を返すことである。

```
! git grep _apply_codd_gate -- tools/agent-project
```

`git grep` はマッチが1件でもあれば exit 0、皆無なら非 0 を返す。先頭の `!`（agent-project の verify 記法で
「非 0 を期待する」否定）がこれを反転させるので、`_apply_codd_gate` が1件も残っていないときにだけ全体が
exit 0（PASS）になる。この関数（`agent_project/configfile.py` の `_apply_codd_gate_auto_wiring`）はパッケージ内に
しか現れず、sibling の `codd_gate_*.py` や tests には出ない（それらは `resolve_codd_gate` などを正当に持つ）。
だから受入パスを `tools/agent-project` に置いたまま、パッケージから自動配線関数を消せば exit 0 に届く。
run 受入と同じ単一パターンで足り、パスを `agent_project/` へ絞る必要はない。

より厳しく、パッケージからの名指し・import の不在まで見たいなら、パスをパッケージに絞って次を使う。

```
! git grep -nE '_codd_gate|import codd_gate' -- tools/agent-project/agent_project
```

パスを `tools/agent-project` 全体へ広げると、sibling の `resolve_codd_gate` や tests の `import codd_gate_debt`
まで巻き込み、境界が禁じていない任意部品を違反扱いにしてしまう。だからこの広いパターンはパッケージ
（`agent_project/`）限定で使い、正典の完了条件は上の単一パターンに固定する（両者を1本の連結パターンに混ぜない。
`_apply_codd_gate` は `_codd_gate` に包含されるため連結は冗長でもある）。コメントや docstring 中の `codd-gate`
への言及まで消す必要が出る `codd.?gate` の総当たりは、コード結合の禁止という意図を超えるので採らない。

どちらも毎回スキャンし直す決定的判定で（不変条件 1「no fake green」と同じ規律）、境界が守られているかを
人の読解ではなく exit code で決める。§4.1「有効化／永続化」が説明する状態、つまりパッケージから
`_apply_codd_gate_auto_wiring` と doctor/model の `import codd_gate_*` を除いた状態にすれば PASS する。除去そのものは
実装側の後続タスクで扱い、本節はドキュメント上の完了条件を定義するに留める。

## 5. codd-dev からの主な翻案（差分）

| codd-dev | codd-gate | 理由 |
|----------|-----------|------|
| requirements（機能要件）を起点に build | **charter（指針）を起点にしない**。要件分解は agent-project の plan の領分で、codd-gate は成果物間の整合だけを見る | 要求が「機能要件ではなく指針」のため。greenfield の `build` は翻案しない |
| 自前で build/test を実行して root cause 解析 | 実行は agent-project の verify / regression に委譲 | ループ制御・予算・検収は既存の制御層が持っている |
| profiles/adapters（言語知識をコアから排除） | repos レジストリの per-repo グロブ（docs/tests/code）＋固定の推定規則 | 単一ファイル・stdlib の範囲で同じ狙い（コアに言語知識を持たせない）を実現 |
| MCP サーバー / git hooks | CLI ＋ agent-project フック（hooks は README の運用例） | プラグイン境界を「決定的フック」に一本化 |
| 単一リポジトリ前提 | 複数 repo（(url, path, base) identity・repo プレフィックス参照・Followup 分類） | 要求 4 |

## 6. 制約と将来拡張

- **ノード粒度はファイル単位**（v1）。ドキュメントの見出しアンカー・コードのシンボル粒度は
  将来拡張（マップ形式に `anchor` を足す後方互換の道を残してある）。
- **import 解決は Python のみ**構文対応（他言語はパス文字列・注釈・命名規約で接続する）。
  言語を足すときは `extract_refs` に閉じて足す。
- **未解決 repo の実体化は `--sync` opt-in**（既定はローカル読み取りのみ・ネットワークゼロ）。
  実体化は mirror-cache パターン準拠で、フル clone はしない（§3「git アクセスの原則」）。
- doc→doc の接続はエッジとして張るが Amber 判定には使わない（ドキュメント間の伝搬は将来課題）。
