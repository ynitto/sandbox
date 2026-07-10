# codd-gate

**ドキュメント・コード・テストの一貫性を機械的に護る、単体で動く決定的ゲート。**
[CoDD (Coherence-Driven Development)](https://github.com/yohey-w/codd-dev) の設計 —
**Trace（接続マップ）/ Impact（Green・Amber・Gray 分類）/ Verify（偽グリーンを許さない検証）** —
を翻案した CLI。依存は **python3 と git のみ**（pip 依存なし・LLM 不要・必ず有限時間で終わる）。

> - 設計の正典: [`docs/designs/codd-gate-design.md`](../../docs/designs/codd-gate-design.md)
> - **kiro-project から完全に独立**。charter.md は読まず、他ツールと共有するのは
>   [`schemas/`](../../schemas/README.md) の共通データ契約（repos / task）だけ。
>   CI・git hook・手元の点検にそのまま使う。
> - **どのサブコマンドも単発・有界**（watch/daemon を持たない。git 呼び出しも個別タイムアウト）。
>   「常に」の繰り返しは cron・git hook・CI が持つ。
> - kiro-project と組み合わせると「ドリフトの自動修復ループ」に発展する — 追加情報として
>   巻末の[連携付録](#付録-kiro-project-との連携オプション)にまとめた（連携は一方向のオプション）。

## 何を解決するか（ブラウンフィールド前提）

コードだけが直され、ドキュメントとテストが置き去りになる——これを**変更の受け入れ前に**機械的に
捕まえる。既存の負債（壊れた参照・未文書化・未テスト）は棚卸しして漸進的に返す。

| CoDD の機能 | codd-gate での対応物 |
|-------------|--------------------|
| Trace（接続マップ） | `scan` — doc↔code↔test のエッジと既存負債の棚卸し |
| Impact（波及分類） | `impact` — 差分を **Green / Amber / Gray / Followup** に分類 |
| Verify（no fake green） | `verify` — マップのキャッシュを信用せず**毎回スキャン**して差分と突合（exit 0/1） |
| Fix（修正の伝搬） | 所見（`impact --json` / `verify --debt --json`）が正。`tasks` は所見を**共通 task スキーマ**（`schemas/task.schema.json`）の修復タスクとして出力（消化は外部） |

**ブラウンフィールドの鉄則**: 既存負債で常時 NG にしない。

- **差分ゲート**（`verify --base`）… 「この変更が**新しく壊した／置き去りにした**分」だけを NG にする
- **負債ラチェット**（`verify --debt --max-broken N`）… 既存負債は棚卸しして上限と突合。改修が進むたび N を下げる
- **負債のタスク化**（`tasks --debt`）… 未文書化・未テスト・壊れた参照を修復タスクとして書き出す

---

## 単体ツールとして使う

### インストール

```bash
bash tools/codd-gate/install.sh           # ~/.local/bin/codd-gate（--prefix で変更可）
# インストールしなくても python3 tools/codd-gate/codd-gate.py ... で代用可
# kiro-project 利用者は tools/kiro-project/install.sh が codd-gate も同梱インストールする
```

### クイックスタート（単一リポジトリ）

```bash
cd <repo>
codd-gate scan                            # 接続マップと負債の棚卸し（.codd-gate/map.json）
codd-gate impact --base origin/main       # この差分はどこに波及するか（報告のみ）
codd-gate verify --base origin/main       # 一貫性ゲート（ドリフトがあれば exit 1）
codd-gate verify --debt --max-broken 0    # 負債ラチェット（棚卸し件数をしきい値と突合）
codd-gate tasks  --base origin/main       # ドリフト → 修復タスク（JSON。--inbox DIR でファイル出力）
```

### CI / git hook に組み込む（「常に」の単体運用）

```bash
# pre-push hook（自分が push する差分だけを見る）
echo 'codd-gate verify --base "@{push}"' >> .git/hooks/pre-push && chmod +x .git/hooks/pre-push
```

```yaml
# GitHub Actions / GitLab CI（PR/MR の差分ゲート＋負債ラチェット）
- run: |
    python3 tools/codd-gate/codd-gate.py verify --base "origin/${BASE_BRANCH}"
    python3 tools/codd-gate/codd-gate.py verify --debt --max-broken 0
```

### 複数リポジトリ（パス＋ブランチで一意）

リポジトリのレジストリは**設定ファイルの `repos:`**（codd-gate ネイティブ。外部フォーマット非依存）。
同じ形を独立ファイルに切り出して `--repos <file>`（設定 `repos_file:`）で渡すこともでき、その形式は
ツール横断の共通スキーマ [`schemas/repos.schema.json`](../../schemas/repos.schema.json) — kiro-project
の `<root>/repos.{yaml,json}`（charter しか無い環境では kiro-project が charter から自動生成する）
と**同じファイルを共有**できる。identity は **(url, path, base)** —
モノレポは path 別、ブランチ別は base 別のエントリで区別する。
設定ファイルは `.kiro/codd-gate.{yaml,yml,json}`（探索順: `--config` → `./.kiro/` → `~/.kiro/`。
YAML は PyYAML 任意・無ければ JSON）。

```yaml
# .kiro/codd-gate.yaml
repos:
  app:
    url: git@example.com:team/app.git   # 任意（表示・リポジトリ横断参照の識別用）
    base: main                           # 任意（記録用ブランチ。作業ブランチの一致は強制しない）
    dir: .                               # ローカル checkout
    docs: [docs/**, README.md]           # 分類グロブの上書き（docs / tests / code。省略時は既定）
    tests: [tests/**]
  handbook:
    url: git@example.com:team/handbook.git
    base: main
    dir: ../handbook
  shop-api:                              # モノレポ: 同じ url を path 別に分ければ別 repo（別ノード空間）
    url: git@example.com:team/shop.git
    path: apps/api
    dir: ../shop
map: .codd-gate/map.json                 # scan の書き出し先（任意）
```

ローカル checkout は CLI の `--repo-dir <name>=<dir>`（複数可）が設定の `dir:` より勝つ。
**ディレクトリが解決できない repo で黙って PASS しない**（チェック対象に選べば exit 2）。
レジストリ無しならカレントディレクトリを単一 repo `default` として扱い、`--repo-dir` だけでも
アドホックに複数 repo を並べられる。

```bash
codd-gate scan                                        # 設定の repos: を使う
codd-gate verify --repo app --base origin/main        # 差分を取る repo を選ぶ（複数 repo 時）
codd-gate scan --repo-dir a=. --repo-dir b=../b       # 設定ファイル無しのアドホック運用
codd-gate scan --sync                                 # dir 無し repo を最新 base で実体化して含める
```

### git アクセスの原則（リモート負荷と鮮度）

- **通常動作はネットワークに一切出ない**: clone も fetch もせず、ローカル読み取り
  （`ls-files` / `diff` / `rev-parse` / `status` / `log -1`）だけ。**フル clone はどの経路にも無い**。
- **`--sync`（opt-in・設定 `sync: true`）**: `dir:` の無い url-only repo を判定に含めたいときだけ使う。
  共有 bare ミラー（初回のみ `--mirror --filter=blob:none`、以後は**増分 fetch**）から
  **detached worktree** を生やして**最新の base** で実体化し、run 後に worktree だけ回収する
  （ミラーは残る＝次回は増分だけ）。ミラー root は `KIRO_GIT_CACHE_DIR`（既定
  `$TMPDIR/kiro-git-cache`）で kiro ツール群と共有。パターンの正典は
  [`docs/designs/git-worktree-cache-pattern.md`](../../docs/designs/git-worktree-cache-pattern.md)。
  鮮度は INV-1（毎回 fetch → fetch 後の SHA で worktree）で保証し、実体化できない repo で
  黙って PASS 側に倒さない（チェック不能なら exit 2）。
- **`dir:` 指定の repo には fetch も clone もしない**: 差分ゲートの判定対象は**作業ツリーそのもの**
  （いま手元にある変更）だから。負債を「リモートの最新 base 起点」で測りたい参照 repo は、
  dir でなく url＋`--sync` で与える。

### 分類ルール（Impact / Verify）

差分（`--base <rev>`..作業ツリー、staged/unstaged/未追跡込み）の各ファイルを判定する。

| 分類 | 意味 | verify |
|------|------|--------|
| **Green** | 変更されたコードの接続先（同一 repo のドキュメント）も同じ差分で更新済み。参照は全て解決 | PASS |
| **Amber** | ドリフト: ①コードが変わったのに接続されたドキュメントが未更新（doc-stale）②変更ファイルに壊れた参照（broken-ref）③削除されたファイルを参照したままの doc/test（dangling-ref） | **NG** |
| **Gray** | 変更されたがドキュメントにもテストにも接続の無いコード（未接続＝地図の空白） | 既定 PASS・`--strict` で NG |
| **Followup** | 接続先ドキュメントが**別リポジトリ**にある（この差分では検証不能） | 既定 PASS・`--strict-cross` で NG。`tasks` が追随タスクを生成 |

テストは「未更新だと Amber」にはしない（コード変更でテストが変わらないのは正常。テストの実行是非は
テストランナー/CI の領分）。テスト接続は**未テスト負債**（`--debt`）と**削除追随**（dangling-ref）にだけ使う。

### 接続の推定（決定的・注釈が最優先）

| 経路 | 例 | エッジ |
|------|----|--------|
| 明示注釈（どのファイルでも） | `<!-- coherence: code=src/util.py -->` / `# coherence: doc=docs/util.md` / `coherence: test=tests/test_x.py` | 宣言どおり（最優先） |
| ドキュメントのインラインコード | `` `src/util.py` `` | doc → code（documents） |
| ドキュメントの md リンク | `[設計](docs/arch.md)` | doc → 対象 |
| テストの import（Python） | `from src.util import helper` | test → code（tests） |
| テストの命名規約 | `test_util.py` ↔ `util.py`（同一 repo で一意のときだけ） | test → code |
| repo プレフィックス | `` `lib:core/engine.py` ``（レジストリの repo 名） | リポジトリ横断エッジ |

- コードフェンス（``` … ```）内は拾わない（サンプルコードの誤検出防止）。フェンス内の参照を接続したいときは注釈を使う。
- `/` を含む参照が解決できなければ**壊れた参照**（broken_refs）。単語だけの曖昧なトークンは負債にしない。

### check（状態アサーション）

「履歴でなく望む最終状態を見る」ための決定的アサーション。修復タスクや CI の合格条件に使う。

```bash
codd-gate check --repo-dir app=. --doc docs/util.md --code src/util.py --fresh
    # 接続がある・doc の参照が全て解決・doc が code より新しい（未コミット変更は「今」とみなす）
codd-gate check --repo-dir app=. --refs docs/util.md          # 参照が全て解決する
codd-gate check --repo-dir app=. --covered src/util.py --need doc,test   # 接続の存在
```

### CLI 一覧

| コマンド | 役割 | exit |
|----------|------|------|
| `scan` | 接続マップ＋負債棚卸し（`--map` へ JSON 書き出し） | 0 |
| `impact --base REV` | 差分の Green/Amber/Gray/Followup 分類（報告のみ） | 0 |
| `verify --base REV` [`--strict --strict-cross`] | 差分ゲート | 0=PASS / 1=NG / 2=使い方 |
| `verify --debt` [`--max-broken --max-undocumented --max-untested`] | 負債ラチェット | 同上 |
| `tasks` [`--base REV`\|`--debt [--cohort]`] [`--inbox DIR`] | 所見→**共通 task スキーマ**の修復タスク（--cohort=同種負債を pilot-then-batch に集約）。所見そのものは `impact --json` / `verify --debt --json` | 0 |
| `check` [`--doc --code --fresh`\|`--refs`\|`--covered --need`] | 状態アサーション | 0/1 |

共通フラグ: `--config` `--repos FILE` `--repo-dir NAME=DIR`（複数可） `--sync` `--map` `--json`。
`--base` 省略時は環境変数 `$KIRO_BASE_REV` を読む（連携用。単体では明示指定が普通）。

---

## 付録: kiro-project との連携（オプション）

ここから先は**追加情報**——単体利用には不要。codd-gate は kiro-project から**完全に独立**しており
（charter.md を読まない・結合は `schemas/` のデータ契約のみ）、組むと「NG を返す」だけでなく
**ドリフトが自動で修復タスクになり消化される**。結合はすべて kiro-project が公式に用意する
**外部 CLI の差し込み点**（正典: `docs/designs/kiro-project-design.md` §4.1）経由で、
双方無改造・外せば元に戻る。**有効化は設定だけ**:

```yaml
# .kiro/kiro-project.yaml — この 2 行＋charter acceptance 1 行で常時運用が立ち上がる
regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV"'   # ① 毎タスクの done 確定前の差分ゲート
intake_cmd: 'codd-gate tasks --debt --repos .kiro-project/projects/default/repos.json'  # ③ 負債の自動返済
```

| 使う差し込み点（設計書 §4.1 の番号） | 拡張する機能 | codd-gate のコマンド |
|--------------------------------------|--------------|---------------------|
| E2 `regression_cmd`（S3 検証ゲートの後・done 確定前） | 検証ゲート（全タスク横断の検査） | ① `verify --base "$KIRO_BASE_REV"` |
| E1 charter `## acceptance`（プロジェクト evaluate） | プロジェクト受入判定 | ② `verify --debt --max-*` |
| E3 `intake_cmd`（S0 取り込み・watch idle） | backlog の自走（pull 型供給） | ③ `tasks --debt [--cohort]` |
| E1 タスクの `- verify:`（S3） | done の根拠 | ④ `check …` / `verify --base …` |

charter は機能要件ではなく**機能追加・リファクタリング・リアーキテクチャの指針**として書き、
一貫性の維持は codd-gate が機械的に担う。

**タスク追加の責務境界**: kiro-project は**元よりタスクを入力とする設計**（enqueue/inbox は
「汎用の取り込み口」で、外部ソースは薄いアダプタで流し込む思想。タスク書式の正典は
`backlog.md.example`）。その JSON 表現は**共通 task スキーマ**（`schemas/task.schema.json`）として
独立管理されており、codd-gate の `tasks` は所見をこの**共通スキーマへ直接出力する**——
「kiro-project 向けアダプタ」ではない（スキーマを読める消化先なら何でもよい）。
スキーマ外の消化先（issue tracker 等）が必要なら、所見 JSON から変換する。

**レジストリ共用**: **共通スキーマの独立ファイル**（`schemas/repos.schema.json`）を両ツールで指す。
kiro-project は `<root>/repos.{yaml,json}` を読み、**無ければ charter の `## repos` から
自動生成する**（`_meta` マーカー付き・正は charter のまま追従）——codd-gate はその生成物を
`--repos` で読むだけで、charter を一切知らない。identity (url, path, base) も共通。

**① 差分ゲート（done 確定前・毎タスク）** — `regression_cmd` に差し込む。verify PASS 後・
done 確定前に走り、ドキュメント置き去りの done を止める（`$KIRO_BASE_REV` は kiro-project が
verify/regression に渡す act 前 HEAD）。

```yaml
# .kiro/kiro-project.yaml
regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV"'
```

タスク単位なら verify そのもの・または追加条件に使う:

```bash
kiro-project enqueue --title "util を高速化" \
  --verify 'pytest -q tests/ && codd-gate verify --base "$KIRO_BASE_REV"'
```

**② 負債ラチェット（プロジェクト done の受入条件）** — charter の acceptance に置く。
数値を段階的に下げれば「整合性を取りつつ改修していく」がプロジェクトの done 条件そのものになる。

```markdown
## acceptance
- `codd-gate verify --debt --max-broken 0 --max-undocumented 12`
```

**③ 負債の自動返済（intake_cmd）** — kiro-project の取り込みコマンドに登録すると、watch の
周期（`intake_interval` 既定 600 秒）で `tasks --debt` が呼ばれ、負債が backlog へ**冪等に**積まれる
（タスク `id` は発見内容から決定的に生成され、現役 backlog に居る発見は再投入されない）。
codd-gate 側は呼ばれるたびに**単発で終わる**——常駐は kiro-project だけが持つ。手動でも流せる:

```bash
codd-gate tasks --debt | kiro-project enqueue --json          # 既存負債を積む（単発）
codd-gate tasks --base origin/main --inbox .kiro-project/projects/default/inbox/
```

**後段のタスク分解**: 負債は 1 発見 = 1 タスク（小さく・個別に verify 可能）で出す。未文書化・未テストの
ような**同種作業の山**は `tasks --debt --cohort` で repo 単位の cohort（kiro-project の
pilot-then-batch: 1 件を人の検収で固めてから残りを自動展開）にまとめ、分解と展開を kiro-project に
委ねられる。巨大な「全部直す」タスクは決して生成しない。

生成されるタスクは kiro-project の鉄則に沿う:

- 同一 repo のドリフト → 決定的 verify 付き（`codd-gate check --doc … --code … --fresh`）＋ `- expect: changes`
- **別 repo への追随** → `- accept:`（自然言語）＋ `- workspace: <repo名>` ＋ `- paths:` を付けて
  kiro-project のワークスペース・ルーティングに乗せる（verify 合成 or 人へ、既存機構のまま）
- 未文書化/未テスト → `codd-gate check --covered <path> --need doc|test` を verify に

**④ タスク単位の verify** — 修復タスクの done 根拠は `codd-gate check`（状態アサーション）。
通常タスクにも `--verify 'pytest -q && codd-gate verify --base "$KIRO_BASE_REV"'` のように併記できる。
単体運用の git hook / CI とはそのまま併用できる（同じコマンド・同じ判定）。

## テスト

```bash
python -m unittest discover -s tools/codd-gate/tests
```

レジストリ読み取り / 分類 / 接続マップ（注釈・インライン・import・命名規約・リポジトリ横断）/
壊れた参照 / 差分分類（green・amber・gray・followup・削除追随）/ 負債ラチェット /
タスク生成（同一 repo verify・別 repo accept+workspace・inbox）/ check（refs・covered・fresh）を網羅。
