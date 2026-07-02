# codd-gate — 設計書

> 最終更新: 2026-07-02 ／ 関連: `tools/codd-gate/`（`codd-gate.py` / `README.md` / `tests/`）,
> `tools/kiro-autonomous/`, `docs/designs/kiro-autonomous-design.md`
>
> 本書は codd-gate の**唯一の設計正典**。実装と差が出たら本書を更新する。

`codd-gate` は、**ドキュメント・コード・テストの一貫性を「受け入れ前のゲート」と「負債の棚卸し→
タスク化」で常時維持する**決定的ツール。[CoDD (Coherence-Driven Development)](https://github.com/yohey-w/codd-dev)
の設計（Trace / Impact / Verify、no fake green）の翻案。

**位置づけ（依存の向きと常駐の所在）**: codd-gate は **kiro-autonomous に依存しない独立ツール**
（依存は python3 と git のみ）。単体で CI・git hook・手元の点検に使え、独立したインストーラ
（`tools/codd-gate/install.sh`）と使い方ドキュメント（`tools/codd-gate/README.md`）を持つ。
kiro-autonomous との関係は**一方向のオプション連携**: 結合は kiro-autonomous 側の**汎用**フック
（regression_cmd / intake_cmd / acceptance。§4）経由で、本体は codd-gate を知らない（コマンド文字列と
JSON 契約だけで結合し、フックはどの決定的ゲート/検出器にも使える）。導線として kiro-autonomous の
install.sh は隣に codd-gate があれば同梱インストールし、**有効化は設定だけ**で完了する。

**役割分担の固定（長期実行の禁止）**: **常駐（長期実行）は kiro-autonomous 側だけが持ち、codd-gate の
サブコマンドはすべて単発・有界**（watch/daemon を持たない。git 呼び出しも個別タイムアウト）。
「一つのサブコマンドが長期タスクにならない」を両ツールの境界として固定する——だから「常に一貫性をとる」の
繰り返しは intake_cmd / regression_cmd / acceptance（連携時）か cron・git hook・CI（単体時）が担い、
codd-gate は呼ばれるたびに 1 パスで判定して終わる。

**設計の出発点（要求）**:

1. ドキュメント・コード・テストの一貫性を**常に**とる（変更のたび・プロジェクトの受入時の両方）
2. kiro-autonomous に**プラグインのような形**で取り込める（本体のコードに手を入れない）。
   かつ単体でも完結して使える
3. **ブラウンフィールド**が主戦場 — ドキュメント・コード・テストは一部だけ存在し、それを整合性を
   取りつつ改修していく。要件は機能要件ではなく、**機能追加・リファクタリング・リアーキテクチャの
   指針（charter）**として与えられる
4. 成果物を格納するリポジトリは**複数**あり、**パス＋ブランチで一意**に決まる

---

## 1. 全体像

```
                 charter.md（指針 goal/constraints ＋ ## repos = レジストリ）
                        │  （codd-gate 専用キー docs:/tests:/code: を repo エントリに追記できる）
      ┌─────────────────┴───────────────────────────────────────────────┐
      │ kiro-autonomous（無改造）                                        │
      │   plan → execute（act=kiro-flow）→ verify ゲート → done          │
      │                          │verify/regression に $KIRO_BASE_REV    │
      │        ┌─────────────────▼─────────────┐                        │
      │        │ ① codd-gate verify --base …   │ ← 差分ゲート（毎タスク） │
      │        └───────────────────────────────┘                        │
      │   acceptance（プロジェクト受入）                                  │
      │        ┌───────────────────────────────┐                        │
      │        │ ② codd-gate verify --debt …   │ ← 負債ラチェット         │
      │        └───────────────────────────────┘                        │
      │   backlog ◀── ③ codd-gate tasks（enqueue --json / inbox）        │
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
| Fix（伝搬） | `tasks` | 修復タスクを生成し **実行は kiro-autonomous → kiro-flow に委譲** |
| —（CoDD に無い） | `check` | 修復タスクの verify 用の状態アサーション（接続・参照解決・鮮度） |

### 不変条件（kiro-autonomous の 5 か条に従属する）

1. **判定は「現在の状態と差分」だけから決める。** マップファイルはキャッシュ/可視化用であり、
   verify は毎回スキャンし直す（stale なマップで偽 PASS を作らない＝no fake green）。
2. **成果の無い場所で偽判定しない。** チェック対象 repo のローカル checkout が解決できなければ
   exit 2 で止まる。未解決 repo は「未スキャン」として明示され、黙って PASS 側に倒れない。
3. **ブラウンフィールドの既存負債で止めない。** 差分ゲートは「この変更が新しく壊した/置き去りに
   した分」だけを NG にする。既存負債は `--debt` の棚卸しとラチェット（`--max-*`）で漸進的に返す。
4. **決定的・stdlib のみ・LLM 不要。** 接続の推定は注釈＞構文（バッククォート/リンク/import/命名規約）
   の固定規則。修復の知能（何をどう書き直すか）は kiro-autonomous → kiro-flow へ委譲する。
5. **安全ゲートは「足す/止める」方向のみ。** codd-gate が done を作ることはない（kiro-autonomous の
   「done は verify の exit 0 のみ」の鉄則に、NG 側の条件として上乗せされるだけ）。
6. **どのサブコマンドも単発・有界。** watch/daemon を持たず、必ず 1 パスで終了する（git 呼び出しも
   個別タイムアウト）。常駐・繰り返しは kiro-autonomous（intake_cmd / regression_cmd / acceptance）や
   cron・git hook・CI の側に置く。

---

## 2. データモデル

### ノード＝成果物、識別子は「repo 名 : repo 相対パス」

リポジトリの identity は kiro-autonomous と同一の **(url, path, base)**＝「パス＋ブランチで一意」。
charter `## repos` のエントリ名がノード識別子のプレフィックスになる（モノレポは path 別エントリ、
ブランチ別は base 別エントリで、それぞれ**別 repo 名＝別ノード空間**になる）。

```
ノード:  app:src/util.py            kind ∈ {doc, code, test}（other は対象外）
エッジ:  {src, dst, kind ∈ {documents, tests}, evidence="docs/x.md:12 (inline)"}
負債:    broken_refs（解決できない参照）/ orphans.undocumented / orphans.untested
```

### 分類（kind）— repo ごとに charter で上書き可能

`doc（拡張子 .md/.rst/.adoc または docs グロブ） > test（tests グロブ・test_*/․test.* 規約） >
code（code グロブ、未指定なら拡張子表）> other` の優先順。codd-gate 専用キー
`- docs:` `- tests:` `- code:` は charter の repo エントリに書け、kiro-autonomous のパーサは
未知キーとして無害に無視する（確認済みの互換性が本プラグイン方式の前提）。

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
repos 解決（charter ## repos ＋ --repo-dir） → 各 repo の git ls-files（＋未追跡、
作業ツリーに実在するものだけ） → kind 分類 → 参照抽出 → 解決 → ノード/エッジ/負債
→ map.json（可視化・棚卸し用。判定のキャッシュにはしない）
```

- index に残っていても作業ツリーから消えたファイルは「実在しない」— 削除の追随漏れを
  参照切れとして検出するための规約。
- 各 repo の HEAD と現在ブランチを記録する（監査用。**ブランチ不一致を NG にはしない** —
  kiro-flow は base から作業ブランチ `kf/<run-id>` を切って作業するため、検証時点のブランチは
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
  どうかは kiro-autonomous の verify / regression の領分。テスト接続は未テスト負債と削除追随に使う。
- **Followup を既定 PASS にする理由**: kiro-autonomous では 1 タスク＝1 リポジトリ書込
  （ワークスペース・ルーティング）なので、別 repo のドキュメント追随は同じ差分内では原理的に
  完了できない。NG で止める代わりに `tasks` が追随タスクを生成して backlog に返す
  （「足す」方向の安全ゲート）。

### verify --debt — 負債ラチェット

差分ではなく全体棚卸し（broken_refs / undocumented / untested の件数）を `--max-*` と突合する。
しきい値未指定は報告のみ（exit 0）。charter acceptance に置いて数値を段階的に下げることで、
「整合性を取りつつ改修していく」がプロジェクトの done 条件になる。

### tasks — Fix（実行は委譲）

| 発見 | 生成タスク | done の根拠（kiro-autonomous の鉄則に整合） |
|------|-----------|--------------------------------------------|
| doc-stale（同一 repo） | 「X の変更を doc Y へ反映」 | `check --doc Y --code X --fresh`（状態: 接続・参照解決・鮮度）＋ `expect: changes` |
| broken-ref / dangling-ref | 「Y の壊れた参照を修正」 | `check --refs Y` |
| doc-stale-cross（別 repo） | 「X の変更を repo B の doc Y へ反映」 | `- accept:`（自然言語→kiro-autonomous が verify 合成 or 人へ）＋ `- workspace: B` ＋ `- paths:` |
| unmapped / undocumented | 「X を文書化/接続」 | `check --covered X --need doc` |
| untested | 「X のテストを追加」 | `check --covered X --need test` |

出力は enqueue --json 互換（`id`/`title`/`verify`/`accept`/`priority`/`paths`/`workspace`/`note`/
`expect`。未知キーも kiro-autonomous 側で保持される）。`--inbox DIR` で 1 タスク 1 JSON の
ファイル投入もできる（verify を持つので triage で ready に昇格する）。

**後段のタスク分解を前提にした粒度**: 常に **1 発見 = 1 タスク**（小さく・個別に verify 可能）で出し、
「全部直す」型の長期タスクは生成しない。未文書化・未テストのような**同種作業の山**は
`tasks --debt --cohort` で repo 単位の cohort spec（`cohort_items`＋`{item}` プレースホルダ）にまとめ、
kiro-autonomous の pilot-then-batch（1 件を人の検収で固めてから残りを自動展開）へ分解を委ねる。
タスク `id` は発見内容から決定的に生成（kiro-autonomous の id 規約 [A-Za-z0-9_-]・48 字に収め、
末尾ハッシュで切り詰め衝突を回避）——intake_cmd の冪等キーとして機能する。

### check — 修復タスクの verify 用アサーション

「履歴でなく望む最終状態を見る」の具体化。エッジ存在・参照解決・**鮮度**
（doc の実質最終変更 ≥ code のそれ。未コミット変更は「今」とみなす）を状態としてアサートする。
`git log | grep` 型の履歴 verify を書かせないための部品。

---

## 4. kiro-autonomous との結合点（オプション連携・プラグイン境界）

連携は一方向のオプション（codd-gate 単体でも §3 の全ステージが完結する）。kiro-autonomous 本体は
無改造で、結合はすべて既存の決定的フック経由。外せば元に戻る。

| # | フック | 差し込み | 効き方 |
|---|--------|---------|--------|
| ① | `regression_cmd`（設定/CLI） | `codd-gate verify --base "$KIRO_BASE_REV"` | 毎タスクの verify PASS 後・done 確定前。NG なら done せず人へ（既存の回帰ゲート挙動） |
| ② | charter `## acceptance` | `codd-gate verify --debt --max-broken 0 …` | プロジェクト受入の負債ラチェット。evaluate のたび決定的に判定 |
| ③ | `intake_cmd`（設定/CLI・pull 型の汎用取り込みフック） | `codd-gate tasks --debt` | watch の周期（intake_interval）で負債→修復タスクを**冪等取り込み**（決定的なタスク id が冪等キー）。正準ループが消化（ルーティング・検収・自律度は既存機構のまま）。手動は `enqueue --json` / `inbox/` |
| ④ | タスクの `verify` | `codd-gate check …` | 修復タスクの done 根拠（状態アサーション） |
| （補） | charter `## repos` | レジストリ共用＋専用キー `docs:/tests:/code:` | 二重管理の排除。identity (url, path, base) も共通 |

`$KIRO_BASE_REV` は kiro-autonomous が verify / regression に渡す act 前 HEAD（実装済みの規約）を
そのまま使う。ワークスペース運用（別 repo clone 内での verify 実行）でも、タスク生成時に
`--repo-dir <name>=.` を焼き込むことで clone 内で自己完結する。

## 5. codd-dev からの主な翻案（差分）

| codd-dev | codd-gate | 理由 |
|----------|-----------|------|
| requirements（機能要件）を起点に build | **charter（指針）を起点にしない**。要件分解は kiro-autonomous の plan の領分で、codd-gate は成果物間の整合だけを見る | 要求が「機能要件ではなく指針」のため。greenfield の `build` は翻案しない |
| 自前で build/test を実行して root cause 解析 | 実行は kiro-autonomous の verify / regression に委譲 | ループ制御・予算・検収は既存の制御層が持っている |
| profiles/adapters（言語知識をコアから排除） | charter の repo エントリ per-repo グロブ（docs/tests/code）＋固定の推定規則 | 単一ファイル・stdlib の範囲で同じ狙い（コアに言語知識を持たせない）を実現 |
| MCP サーバー / git hooks | CLI ＋ kiro-autonomous フック（hooks は README の運用例） | プラグイン境界を「決定的フック」に一本化 |
| 単一リポジトリ前提 | 複数 repo（(url, path, base) identity・repo プレフィックス参照・Followup 分類） | 要求 4 |

## 6. 制約と将来拡張

- **ノード粒度はファイル単位**（v1）。ドキュメントの見出しアンカー・コードのシンボル粒度は
  将来拡張（マップ形式に `anchor` を足す後方互換の道を残してある）。
- **import 解決は Python のみ**構文対応（他言語はパス文字列・注釈・命名規約で接続する）。
  言語を足すときは `extract_refs` に閉じて足す。
- **未解決 repo の自動 clone はしない**（v1）。kiro-autonomous の mirror-cache パターン
  （`docs/designs/git-worktree-cache-pattern.md`）への追随は、必要になったときに `--clone` opt-in で
  足す（黙って clone して判定を遅く/不安定にしない）。
- doc→doc の接続はエッジとして張るが Amber 判定には使わない（ドキュメント間の伝搬は将来課題）。
