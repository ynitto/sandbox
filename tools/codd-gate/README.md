# codd-gate

**ドキュメント・コード・テストの一貫性を機械的に護る、単体で動く決定的ゲート。**
[CoDD (Coherence-Driven Development)](https://github.com/yohey-w/codd-dev) の設計 —
**Trace（接続マップ）/ Impact（Green・Amber・Gray 分類）/ Verify（偽グリーンを許さない検証）** —
を翻案した CLI。依存は **python3 と git のみ**（pip 依存なし・LLM 不要・必ず有限時間で終わる）。

> - 設計の正典: [`docs/designs/codd-gate-design.md`](../../docs/designs/codd-gate-design.md)
> - **kiro-autonomous への依存は無い**。単体で CI・git hook・手元の点検に使える（本書の前半）。
>   kiro-autonomous と組み合わせると「ドリフトの自動修復ループ」に発展する（後半の連携節。
>   連携は一方向のオプションで、kiro-autonomous 本体は無改造）。

## 何を解決するか（ブラウンフィールド前提）

コードだけが直され、ドキュメントとテストが置き去りになる——これを**変更の受け入れ前に**機械的に
捕まえる。既存の負債（壊れた参照・未文書化・未テスト）は棚卸しして漸進的に返す。

| CoDD の機能 | codd-gate での対応物 |
|-------------|--------------------|
| Trace（接続マップ） | `scan` — doc↔code↔test のエッジと既存負債の棚卸し |
| Impact（波及分類） | `impact` — 差分を **Green / Amber / Gray / Followup** に分類 |
| Verify（no fake green） | `verify` — マップのキャッシュを信用せず**毎回スキャン**して差分と突合（exit 0/1） |
| Fix（修正の伝搬） | `tasks` — ドリフトを修復タスク（JSON）に変換（消化は人・CI・kiro-autonomous 等の外部） |

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

リポジトリのレジストリは **`## repos` セクションを持つ Markdown**（`--charter` で指定。
kiro-autonomous の charter と同書式だが、codd-gate 単体ではただのレジストリファイルとして書けばよい）。
identity は **(url, path, base)** — モノレポは path 別、ブランチ別は base 別のエントリで区別する。

```markdown
## repos
- app = git@example.com:team/app.git
  - desc: アプリ本体
  - base: main
  - docs: docs/**, README.md      # 分類グロブの上書き（docs / tests / code）
  - tests: tests/**
- handbook = git@example.com:team/handbook.git
  - desc: 運用ドキュメント（app の仕様章はここ）
  - base: main
```

ローカル checkout との対応は `--repo-dir <name>=<dir>`（複数可）か設定ファイルで与える。
**ディレクトリが解決できない repo で黙って PASS しない**（チェック対象に選べば exit 2）。
レジストリ無しならカレントディレクトリを単一 repo `default` として扱う。

```bash
codd-gate scan --charter repos.md --repo-dir app=. --repo-dir handbook=../handbook
codd-gate verify --charter repos.md --repo-dir app=. --repo-dir handbook=../handbook \
  --repo app --base origin/main
```

毎回フラグを書かないための設定ファイル `.kiro/codd-gate.{yaml,json}`
（探索順: `--config` → `./.kiro/` → `~/.kiro/`。YAML は PyYAML 任意・無ければ JSON）:

```yaml
charter: repos.md          # レジストリ（## repos を持つ Markdown）
repo_dirs:
  app: .
  handbook: ../handbook
map: .codd-gate/map.json
```

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
| `tasks` [`--base REV`\|`--debt`] [`--inbox DIR`] | 修復タスク生成（JSON） | 0 |
| `check` [`--doc --code --fresh`\|`--refs`\|`--covered --need`] | 状態アサーション | 0/1 |

共通フラグ: `--charter`（レジストリ Markdown） `--config` `--repo-dir NAME=DIR`（複数可） `--map` `--json`。
`--base` 省略時は環境変数 `$KIRO_BASE_REV` を読む（kiro-autonomous 連携用。単体では明示指定が普通）。

---

## kiro-autonomous との連携（オプション・プラグイン）

kiro-autonomous と組むと「NG を返す」だけでなく**ドリフトが自動で修復タスクになり消化される**。
結合点は 4 つ、すべて kiro-autonomous の既存フック（**本体無改造**・外せば元に戻る）。
charter は機能要件ではなく**機能追加・リファクタリング・リアーキテクチャの指針**として書き、
一貫性の維持は codd-gate が機械的に担う。

レジストリは kiro-autonomous の charter `## repos` をそのまま共用する（二重管理しない）。
codd-gate 専用キー `- docs:` `- tests:` `- code:` は kiro-autonomous には未知キーとして無害に無視される。

**① 差分ゲート（done 確定前・毎タスク）** — `regression_cmd` に差し込む。verify PASS 後・
done 確定前に走り、ドキュメント置き去りの done を止める（`$KIRO_BASE_REV` は kiro-autonomous が
verify/regression に渡す act 前 HEAD）。

```yaml
# .kiro/kiro-autonomous.yaml
regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV"'
```

タスク単位なら verify そのもの・または追加条件に使う:

```bash
kiro-autonomous enqueue --title "util を高速化" \
  --verify 'pytest -q tests/ && codd-gate verify --base "$KIRO_BASE_REV"'
```

**② 負債ラチェット（プロジェクト done の受入条件）** — charter の acceptance に置く。
数値を段階的に下げれば「整合性を取りつつ改修していく」がプロジェクトの done 条件そのものになる。

```markdown
## acceptance
- `codd-gate verify --debt --max-broken 0 --max-undocumented 12`
```

**③ ドリフト・負債の backlog 化** — `tasks` の出力をそのまま enqueue / inbox へ。

```bash
codd-gate tasks --debt | kiro-autonomous enqueue --json          # 既存負債を積む
codd-gate tasks --base origin/main --inbox .kiro-autonomous/projects/default/inbox/
```

生成されるタスクは kiro-autonomous の鉄則に沿う:

- 同一 repo のドリフト → 決定的 verify 付き（`codd-gate check --doc … --code … --fresh`）＋ `- expect: changes`
- **別 repo への追随** → `- accept:`（自然言語）＋ `- workspace: <repo名>` ＋ `- paths:` を付けて
  kiro-autonomous のワークスペース・ルーティングに乗せる（verify 合成 or 人へ、既存機構のまま）
- 未文書化/未テスト → `codd-gate check --covered <path> --need doc|test` を verify に

**④ 常時運用** — `run --watch` の周期で `tasks --debt --inbox` を cron 等から流す
（単体運用の git hook / CI と併用できる）。

## テスト

```bash
python -m unittest discover -s tools/codd-gate/tests
```

レジストリ読み取り / 分類 / 接続マップ（注釈・インライン・import・命名規約・リポジトリ横断）/
壊れた参照 / 差分分類（green・amber・gray・followup・削除追随）/ 負債ラチェット /
タスク生成（同一 repo verify・別 repo accept+workspace・inbox）/ check（refs・covered・fresh）を網羅。
