# agent-project

> 旧 `kiro-project` 系統から移行した後継実装。設計正典は
> [`docs/designs/agent-project-design.md`](../../docs/designs/agent-project-design.md)。
> 改称方針: [`docs/designs/agent-tools-rename-design.md`](../../docs/designs/agent-tools-rename-design.md)。

**単一プロジェクトのバックログを自律的に優先順位付け・実行・検証・収束させ、人の判断が要る分だけ差し戻す制御層。**
カレントディレクトリ（または `--root`）をプロジェクトルートとし、`charter.md` / `repos.json` を入力に
成果物（`archive/`・`DELIVERY.md`・`needs/`・`decisions/`）を出力する。
最優先タスクを agent-flow に実行させ、**返ってきた receipt を検算して PASS したものだけ done に確定**
（`archive/` へ退避）、NG なら積み直す。受入基準の判定は agent-flow の専用 verifier が行い、
agent-project は検証計画（`verification_plan`）を作って receipt を検算する側に徹する。backlog が尽きるか予算が尽きるまで繰り返し、人の判断は案件毎の
`needs/<id>.md`（フィードバック欄つき）で差し出し、判断は `decisions/<id>.md` に残す。

> - 設計の正典（なぜこの形か）: [`docs/designs/agent-project-design.md`](../../docs/designs/agent-project-design.md)
> - 仕様の正典（何ができて何を設定できるか）: [`docs/specs/agent-project-spec.md`](../../docs/specs/agent-project-spec.md)
> - 熟練度別の導入手順: [`GUIDE.md`](GUIDE.md)（L0 下見 → L1 試運転 → L2 日常運用 → L3 無人運用 → L4 スケール）
> - タスク書式の正典: [`backlog.md.example`](backlog.md.example) ／ プロジェクト憲章: [`charter.md.example`](charter.md.example)
> - 実行（コード変更・検証）は agent-flow（＝エージェント CLI）へ委譲する。

## 全体像

役割の異なる 3 層で動く。**構成は「1 プロジェクト = 1 ディレクトリ = 1 プロセス」**。複数プロジェクトは
ディレクトリ（通常は状態リポジトリの clone）を並べてそれぞれで回し、束ねた可視化・操作は
[agent-dashboard](../agent-dashboard/) が git 越しに担う。

| 層 | 担当 | 実体 |
|----|------|------|
| 上位（目標駆動） | 目標(charter)→backlog 生成 / 達成評価 / 改善サイクル | `run`（charter あり） |
| 外側（制御） | 優先順位付け / 検証ゲート / 積み直し / 収束 / 決定記録 / 安全ゲート | `run`（charter 無し） |
| 内側（実行） | タスクの分解 → act → 内側 verify ループ | `agent-flow run`（別ツール） |

> **プロセスは `run` に一本化**。`<root>/charter.md` があれば `run` が自動で目標駆動（plan→execute→evaluate）に入る。
> charter 無しは従来の backlog 消化ループ。`--watch` がそのまま「目標を満たすまで回り続ける常駐」になる。

**正準ループ（5 点）**:

1. `backlog/<id>.md` を読み優先順位をつけ、最優先を agent-flow に投げる。
2. 優先順位付けは `--planner agent`（エージェントが `priority` も加味）/ `none`（priority 降順→最古）。人は `policy.md` で上書きできる。
3. agent-flow の結果を verify ゲートで検証。done は `archive/` へ退避、NG なら積み直す。
4. backlog が尽きるか予算（サイクル/実時間/コスト）が尽きるまで反復（`--watch` なら尽きても監視を続ける）。
5. 人の判断・フィードバックは案件毎 `decisions/<id>.md` に保存する。

> **鉄則**: done は **verify の終了コード 0 のみ**が根拠（自己申告 done の禁止）。必ず有限回で止まる。
> 人の `policy.md` ＞ エージェント提案。本体は標準ライブラリのみ・決定的（知能は agent-flow / エージェント CLI へ委譲）。

## 依存・インストール

- `python3`（標準ライブラリのみ。pip 依存なし）
- `agent-flow`（act の委譲先。PATH か `tools/agent-flow/agent-flow.py` を自動解決。`--dry-run` なら不要）
- エージェント CLI（LLM 呼び出し＝分解・優先順位・裁定・ルーティングに使用。設定 `agent_cli` / CLI `--agent-cli` で切替）
  - **どの CLI をどう起動するかは、すべて定義ファイル `agents/<name>.json` にある**（S9。契約は
    [`schemas/agent-cli.schema.json`](../../schemas/agent-cli.schema.json)）。同梱は
    `kiro`（既定・`kiro-cli chat`）/ `claude` / `copilot` / `codex` / `cursor` / `ollama`。
    コード側に CLI 分岐は無く、**作法が変わったときの修正は JSON 1 ファイルで完結する**
  - 上位のディレクトリ（`$KIRO_AGENTS_DIR` → `<プロジェクト>/agents/` → `~/.agents/agents/`）に
    同名の定義を置けば同梱定義を上書きできる。新しい CLI もファイルを 1 枚足すだけで使える
  - 定義を解決できない `agent_cli` は明示エラー（黙って別の CLI へ倒さない）
  - モデルは設定 `model:` で指定（省略時は各 CLI の既定。実行層 agent-flow 側は agent-flow.yaml の `agent_cli` / `model` で揃える）
  - `agents.<purpose>.fallbacks` に候補を並べると、分類不能な内容失敗の最初の再試行だけ
    `relative_cost` が厳密に大きい先へ一段昇格する。quota・認証・制御・一時障害は昇格せず、
    昇格元・先と係数は budget ledger に残る
  - 処理ごとの権限は `agents[purpose].readonly: true` で宣言できる（既定 false = 従来どおり
    書き込みモード）。宣言してよいのは**読まない系**——材料を全部プロンプトで受け取り
    文章か JSON を返すだけの処理（adjudicate / assess / distill / prioritize / route / plan）。
    読む系（repo_map / doctor / review）に付けると CLI の readonly 実装がツールを削るため
    探索そのものを失う。クラウド CLI では判断だけの呼び出しから
    `--dangerously-skip-permissions` 等が外れる（設計:
    [2026-08-08-agent-ollama-expansion-design.md](../../docs/plans/2026-08-08-agent-ollama-expansion-design.md) §5）

```bash
bash tools/agent-tools/install.sh                         # agent-project / agent-flow / agent-amigos を
                                              # まとめて ~/.local/bin へ（推奨。3 本は同じ
                                              # agentcore と契約バージョンを共有するので
                                              # 別々に入れない）
bash tools/agent-tools/install.sh --only agent-project    # このツールだけ入れ直す
```
未インストールでも `python3 tools/agent-project/agent-project.py ...` で代用可。

## クイックスタート

```bash
mkdir my-proj && cd my-proj                          # プロジェクトルート（cwd）を用意

# バックログへ積む（<root>/backlog に作られる）
agent-project enqueue --title "README に概要見出しを追加" --verify 'grep -q "## 概要" README.md'
agent-project run --executor agent                     # 自律消化

# 目標から回す。charter.md を置けば run が自動で plan→execute→evaluate に入る（専用コマンド不要）
cp tools/agent-project/charter.md.example ./charter.md
agent-project run --executor agent

# 常駐: 新規タスク/フィードバックを監視して自動消化（idle 中はエージェント非起動）
agent-project run --watch --poll 10 --executor agent

# エージェント CLI 無しでプロトコル確認（決定的・無料）
agent-project run --planner none --flow-planner stub --executor stub
```

`backlog/<id>.md` に `- priority: N`（大ほど高）で外部から順序を制御できる。サブコマンド省略
（`agent-project` 単体）は **`run --watch` と同義**（cwd のプロジェクトを常駐監視）。

## ディレクトリ構成（プロジェクトルート直下フラット）

**プロジェクトルート（`--root`・既定 cwd）の直下にすべて集約**される（各パスは `--backlog` 等で個別上書きも可）。

```
<root>/                    ← プロジェクトルート（cwd。通常は状態リポジトリの clone）
  charter.md           プロジェクト憲章（人が書く・最上位入力。正典 charter.md.example）
  repos.yaml|json      リポジトリレジストリ（共通スキーマ schemas/repos.schema.json）。手書きが
                       あればそれが正（charter の ## repos は互換入力）。無ければ charter から
                       repos.json を自動生成（_meta 付き・正は charter に追従）＝codd-gate 等の
                       外部ツールへ「ファイルとして渡す」。charter 無しでもルーティングに効く
  project.json         project のサイクル状態（PASS 履歴・stall・cost。run が増分更新）
  policy.md            優先順位・実行先・安全ゲートの上書き（人だけが書く）
  backlog/<id>.md      タスク本体（案件毎・人が追加できる。done で archive/ へ退避）
  needs/<id>.md        判断待ち/検収待ちの通知＋決定記入欄（MADR 互換 ADR。人が記入→自動再開）
  decisions/<id>.md    人の判断・承認・フィードバックの決定記録（learn＝学習材料。append-only）
  archive/<id>.md      完了タスクの保全先（検収用「納品書」付き。backlog と 1:1）
  DELIVERY.md          納品一覧（受領書）。done を1行ずつ追記
  tombstones.md        墓標（却下・削除したタスク。同じタイトルは再提案しない。人が手で書き足せる。
                       解除は `agent-project revive <タイトル>`）
  notes/*.md           観点メモ（人が書き溜める。plan は自動では消費せず、`distill-notes` のときだけ
                       バックログ候補になる。取り込めたメモは notes/archive/ へ移る）
  journal.md           機械のサイクルログ（人間可読・閾値超過で journal-archive/ へ自動ローテーション。
                       設定 journal_max_bytes（既定 256KB・0 で無効）/ journal_keep（保持世代・既定 20））
                       ／ run-log.jsonl  構造化 run-log（JSON）
  status.json          daemon の生存信号（watch/level/paused/updated_iso）。git 同期経由でリモート
                       viewer の稼働判定に使う（[daemon の生存信号](#daemon-の生存信号statusjson--リモート-viewer-の稼働判定)）
  paused.json          一時停止マーカー（commands の pause で生成・resume で削除）
  inbox/  claims/  autonomy/  bus/   取り込み口 / 原子的クレーム / track 状態 / agent-flow 一時バス
  commands/<name>.json 人の指示（approve/hold/pin/defer/revise/reject/force-complete/replan/revive/
                       distill-notes/heal/pause/resume/stop）のドロップ口（CLI 不要。run/watch が
                       取り込む）
  .state-git/          状態 git 同期の管理クローン（ルートが git でなく state_git 設定時のみ）
```

複数プロジェクトはこのディレクトリを並べ、**PC 単位の常駐体 1 本**（`agent-project serve`）に
まとめて監督させる。どのプロジェクトを持つかは `agent-project.host.yaml` が単一ソースで、
稼働状況は `~/.agents/engine/status.json`（`agent-project status` で読む）に集約される。
導入手順は [常駐一本化セットアップガイド](../../docs/guides/single-resident-setup.md)。

## 実行の委譲（`--location`）

「どこで・どう動かすか」は `--location`（既定 `auto`）に集約。

| location | 委譲方法 | 用途 |
|----------|---------|------|
| `local` | `agent-flow run`（単発・同期） | 既定の実体。この PC で最後まで回す |
| `board` | 委譲公示板へ post（非ブロッキング） | 別ノードへオフロード |

`auto` = offload ポリシー一致 ＋ `board:` 設定あり → board ／ 他は local。どちらの経路でも
verify は act 完了後に走る。

単発 run は自己完結する——orchestrator が自分で生存リースを張り、park（承認待ち）も自分で
面倒を見るので、駆動を代行する常駐プロセスは要らない。PC の電源が落ちて run が非終端のまま
残っても、次に同じ run-id で起動したときリースの失効を見て「停滞」と判定し、失敗ノードだけを
戻して続きから走る（done は温存）。

**非ブロッキング委譲（`board`）**: 板へ公示したタスクは `offloaded` に退避し、次パスで
`result.json` を1回だけポーリングして**終端した委譲だけ**を消化する。`executor: gitlab` のように
MR 承認まで数日かかる委譲でループを塞がず、同じプロジェクトの他タスク・他プロジェクトを並行に
進められる。委譲 id は決定的なので agent-project が再起動しても同じ委譲に再合流する。
`act_timeout` は run 全体の壁時計上限なので既定は `0`（無制限）とし、進捗と失踪は run の
orchestrator lease で判定する。正常終端・一時的な読取不能・期限切れを区別し、期限切れが10秒
続いた run だけを止めて次回は done を温存して再開する。`max_seconds` は進行中の run にも効く。
有限値が必要な運用だけ明示する。agent-flow の
`gitlab.timeout/approved_timeout: 0` と併用すれば、レビュー待ちの誤タイムアウトも起きない。

**並列消費（`--concurrency N`、既定 1）**: 依存解決済みの独立タスクを先頭から最大 N 件 板へ並行
post し、実体の並列は請負側ノードの worker に委ねる。**実行の重い部分だけ並列化し、verify・done/archive・
決定記録・派生生成は逐次のまま**（競合回避）。local 単発 run は逐次。1 サイクル=1 タスクの計上・予算は不変。

**原子的クレーム（二重実行防止）**: 各タスクは実行前に `claims/<id>.lock` を `O_CREAT|O_EXCL` で確保した者だけが
回す。**同じ backlog を複数プロセス/ホストで回しても同一タスクは二度実行されない**。取得後に disk を再検証し、
owner 失踪は TTL 超で奪取、終了で解放。無制限 run の claim は 600 秒ごとに mtime を更新し、
更新時に owner / pid / task が一致しなければ run を止める。一時的なI/O失敗は60秒まで再確認し、
claim を失った試行は task と claim を書き換えない。JSON を書き直さないため、監視側が途中状態を
読む窓も作らない。

**分散移譲（board）**: `board: <委譲公示板>`＋`policy.md` の `offload: <パターン>` 一致タスクは
`board` に解決され、板へ公示する。請負側ノードの常駐体が入札・実行し、結果は次パスで回収する。

請負側（板から仕事を受ける側）は `agent-project.host.yaml` の `board:` で参加を宣言する。
常駐体の board tick（30 秒）が能力宣言 `nodes/<node-id>.json` を書き、dashboard から届いた
指示（引き受け・中止・落札）を板へ反映する——**板へ書くのは常駐体だけ**で、dashboard は
`~/.agents/commands/` へ指示を投函するだけ（[`agent-node-command`](../../schemas/agent-node-command.schema.json)）。
入札の選別（担当リポジトリ・タグ・CLI）は host.yaml の `repos` / `tags` / `agent_cli` が正典。
**プロジェクトを 1 つも持たないワーカーノードは、ノード直轄実行で請ける**（実装計画 §7 R2b）。
flow tick が `~/.agents/flow-node/bus` を唯一の取り込み先にして
`agent-flow participate`（入札・落札・板への報告）を 1 巡させ、受理した run を
`NodeWorkerPool` で実行する。フルノードは従来どおりプロジェクトのバス経由——
同じノードに 2 つ目の取り込み主体を置かないため、ノード直轄実行はワーカーノードでだけ走る。

```bash
agent-project run --executor agent                    # 既定 local（単発 run）
agent-project run --location board --concurrency 3    # 一致タスクを板へ並行 post
```

**executor プラグイン**: `--executor`（設定 `executor`）には組み込みの `agent` / `stub` に加えて、
agent-flow の executor プラグイン名（例 `gitlab`）や `.py` パスをそのまま渡せる。値は `agent-flow run --executor <値>`
へ委譲され、プラグイン固有設定は agent-flow 側の設定（例 `gitlab:` ブロック）で行う。

```bash
agent-project run --executor gitlab               # 各タスクを GitLab イシュー化し approved まで待つ
agent-project run --executor /path/to/my_exec.py  # 任意の executor プラグイン（.py パス）
```

## 検証ゲートと安全（done を守る）

verify は done 確定の唯一の根拠だが機械的合否でしかない。以下のゲートが多層で守る（既定はいずれも最小限）。

### verify を人が書かなくてよくする（acceptance / verify_template）

完了条件の決定的シェルは人には書きにくい。タスクは `verify` の代わりに次を持てる
（「done は機械検証の PASS のみが根拠」の鉄則は不変）:

- **`- verify_template: <名前> :: <引数…>`** … 決定的に展開（**エージェント不要**）。`file-contains :: <path> :: <文字列>` /
  `file-exists :: <path>` / `defines :: <symbol> :: <path>` / `diff-contains :: <文字列>`（act 後の差分・`$AGENT_BASE_REV`）/
  `cmd-succeeds :: <コマンド>`。enqueue 時に即展開。
- **`- acceptance: <受入基準>`（複数行可）** … settle 時に**検証エージェント**が基準ごとに実際にコマンドを
  試行錯誤して充足を確かめ、**判定 + 証跡**を返す（S5）。`- accept:`（自然文 1 行）は 1 項目の
  acceptance として扱う（後方互換）。

```bash
agent-project enqueue --title "規約に最終更新日を表示" --verify-template 'file-contains :: web/terms.html :: 最終更新'
agent-project enqueue --title "概要見出しを追加"       --accept "README に ## 概要 の見出しがある"
# 受入基準は複数書ける（1 つ 1 基準。JSON 表現では配列）
agent-project enqueue --title "起動先を選べるようにする" \
  --acceptance "起動先に宣言済みリポジトリが並ぶ" \
  --acceptance "宣言が無いリポジトリは非活性で理由付きで表示される"
# 人が直す（指定した分で全行を置換。`--acceptance ''` 1 つで全削除）
agent-project revise T1 --acceptance "直した基準A" --acceptance "直した基準B" --reason "レビュー"
```

**誰が書くか**: 通常はバックログ生成時に `backlog-planner`（S6）が書き、計画レビューで人が直す。
人が直したタスクには `- edited: human` が付き、以後の replan で作り直されない。

**なぜ「基準」で、「合成したコマンド」ではないのか**: 以前は自然文の完了条件から決定的シェルコマンドを
LLM が 1 回で合成し、その exit 0 を done の唯一の根拠にしていた。環境差で大半が失敗して人へ倒れるうえ、
合成されたコマンドが「たまたま通る劣化した検証」だったとしても、**人にはそれを見抜く材料が無い**。
人がレビューできるのは基準と証跡であって、コマンドの良し悪しではない。そこで検収票には
「基準 × 証跡（実行したコマンド・出力の要約・参照したファイル）」の表を載せる。

機械的な護り（LLM の善意に依存しない）:

- **フェイルクローズ** — 明示の pass 表明が無い基準は fail
- **証跡必須** — pass なのに実行コマンドも参照ファイルも無い基準は fail へ落とす
- **差分の常設基準** — 「差分が基準の対象範囲に実在すること」が必ず 1 項目入る（何も変えずに全 pass を返せない）
- **「検証不能」はリトライを焼かない** — 環境にツールが無い等は失敗ではなく、理由付きで人へ回す

検証プロンプトと出力契約はスキル `.github/skills/backlog-verifier/` にあり、上位（プロジェクトの
`.github/skills/`）へ置けば全面的に差し替えられる（設定 `verifier_skill` で名前も変えられる）。
検証レポートは `verifications/<task-id>/<rev>.md` に残る。副作用の範囲は `verify_side_effects`
（既定 `workspace`＝作業ツリー内のみ。DB・外部サービスへの書き込みはどの設定でも不可）。

> verify を自分で書ければそれが最良（最も確実・最速の fast path で、検証エージェントを呼ばない）。
> シェルで検証できないものは `acceptance` に書く。

### 成果物レビューは MR/PR が正（remote_review）

**MR は人が明示的に作る**（自動作成はしない）。`agent-project mr-create <task-id> --root <ROOT>`、
または agent-dashboard の検収カードの「MRを作る」で冪等に作成できる（GitLab と GitHub に対応。
トークンは GitLab が `GITLAB_TOKEN` / `GL_TOKEN`、GitHub が `GITHUB_TOKEN` / `GH_TOKEN`。
既存の open MR/PR があれば再利用する）。旧名 `retry-mr` も
同じ動作で受け付ける。MR を作れば検収の正は MR 一本になり、dashboard の検収カードは
「受入基準 × 証跡 + MR リンク」になる。

GitHub PR も `remote_review` の対象で、マージ済みは承認、未マージクローズは却下、
`CHANGES_REQUESTED` レビューは差し戻しとして取り込む。インラインコメントを差し戻しへ
運ぶときは `ファイル名:行番号: コメント` の形式にする。

一方、**成果ブランチの統合は機械の仕事**で、検収承認（done 確定）の瞬間に自動で行う。MR が
あればクリーンな場合だけ API でマージし、MR が無ければ作業ブランチを target へ
fast-forward／競合なしマージで統合する。競合・未解決ディスカッション・API エラー時は done に
せず review を維持するため、原因を解消して同じ「承認して完了にする」を再送すればよい。

人が GitLab の画面で先に MR をマージしていてもよい。承認時に作業ブランチが消えていても、
検証済み revision が target の祖先であれば「統合済み」と判定して完了する。

フォージ側の**決定的シグナル**が決着になる（`remote_review: settle`・既定）:

| フォージ側の事象 | 決着 |
|---|---|
| MR がマージされた | approve（done 確定） |
| MR が未マージでクローズされた | reject |
| `status:changes-requested` ラベル / Changes Requested レビュー | revise（未解決コメントを feedback へ注入） |
| 上記以外（コメントのみ等） | 何もしない（人の明示操作を待つ） |

コメント本文のキーワード推定は使わない——書き手の言い回し 1 つで判定が変わり、変わったことに
気づけないため。差し戻しは「人がラベル / レビュー状態を明示したとき」と定める。
`remote_review: observe` にすると照会結果を journal に残すだけで決着させない（移行用）。
フォージが無い運用では従来どおり dashboard のボタン（または `approve` / `reject`）で決着する。

### タスクに意図と境界を書く（why / desc / scope / out_of_scope / constraints / hints / demo）

verify は「合否」を守るが「やり方・範囲・意図」は縛れない。一般的なバックログ項目に倣った任意の記述
フィールドで、**人のレビュー材料**と**ワーカーの誘導**を同時に強化できる（詳細は
[`backlog.md.example`](backlog.md.example)）:

- `why`（背景・価値）/ `desc`（作業内容の詳細）… 実装の判断基準と具体の指示。
- `scope` / `out_of_scope` … 変更してよい範囲と**やらないこと**。スコープ膨張・過剰実装を防ぐ
  （範囲外の気づきは `@followup` 提案へ誘導される）。
- `constraints` … このタスク固有の制約（`rules.md`=全タスク共通・charter=プロジェクト共通への上乗せ層）。
- `hints` … 実装の手がかり（関連ファイル・参考実装）。
- `demo` … 人の検収観点（検収で何をどう確かめるか。ワーカーにも「人がここを見る」前提が伝わる）。

書けば act 要求文へ整形注入され、実行前レビュー・検収の票（`needs/<id>.md`）にも載る。plan（charter 分解）は
`why` を必ず付けて提案するので、実行前レビューで「なぜこのタスクか」から判断できる。いずれも**誘導であって
完了条件ではない**（done の根拠は verify のみ）。`enqueue --why … --scope …` / `revise <id> --out-of-scope …` で
CLI からも付与・修正できる。

### verify の鉄則と偽 done 対策

`git log | grep refactor` のように **verify が「履歴の絶対状態」を見る**と、過去コミットにマッチして act が
何もしなくても done 確定する。鉄則は **「履歴でなく望む最終状態/差分を assert する」**。3 層で対策:

- **成果参照の真正化（常時）**: DELIVERY/needs の成果参照は **act 前(baseline)以降の新規変更のみ**を載せ、無ければ
  `(変更なし)`（既存コミットを成果物と偽らない）。agent-project 自身の状態ファイルは差分から除外。
- **差分基準（常時）**: verify 実行時に `$AGENT_BASE_REV`（act 前 HEAD）を渡す。`git log $AGENT_BASE_REV..HEAD --grep …`
  旧 `$KIRO_BASE_REV` も後方互換として同じ値を渡す。
  で差分スコープ verify が書ける。
- **no-progress ガード（opt-in）**: `--require-progress` / per-task `- expect: changes` で、verify=PASS でも変更が
  無ければ done せず人へ。正当な無変更は `- expect: none` で opt-out。

### タスクブランチと成果物レビュー（task_branch / delivery_review・既定 on）

- 各タスクの成果は **`ap/<task-id>`** ブランチに集約される（リトライも同一ブランチ。agent-flow の
  workspace `branch` として伝搬）。
- verify PASS 後は**常に検収待ち（review）**になり、人の承認で done 確定する。MR は自動では
  作らない——欲しいときに `mr-create`（dashboard の「MRを作る」）で人が作る。MR があれば承認時に
  **クリーン（コンフリクト無し・未解決レビューコメント無し）なら自動マージ**（差分なしはクローズ・
  未クリーンは差し戻しコメントを付けて review のまま）。MR が無ければ **`ap/<task-id>` → target を
  Git で自動統合**する（統合できなければ done にせず review 維持）。
- 却下（`reject`）は MR をクローズし、作業ブランチを **退避タグ `rejected/<task-id>` へ逃がしてから
  削除**する。取り戻しは `git fetch origin tag rejected/<task-id>`。タグを push できなかったときは
  ブランチを削除しない（消えたら取り返せないため）。
- 従来の自動 done へは `--no-delivery-review`／設定 `delivery_review: false`、ブランチ集約の無効化は
  `--no-task-branch`。

### run ブリーフ（差し戻し意図とノード発見制約の伝播・task_branch 有効時）

分散生成した成果の一貫性は、**事後の集約ノード**（agent-flow の `reduce`/`synthesize` は依存ノードの
全出力を 1 コンテキストへ読む＝規模が大きくなるほどコンテキスト制約で損失的になる）だけには頼れない。
そこで agent-project は、リトライ（差し戻し）の意図と各ノードが実行中に発見した恒常制約を、タスクの
ターゲットブランチ **`ap/<task-id>` と同じキー**で **`<root>/brief/<task-id>.md`**（`rules.md` と同じ
`<root>` 直下）に**追記のみ**で蓄積した「**run ブリーフ**」にまとめ、`build_request` 経由で**以後の
全 run・全分散ノードへ均一に注入**する（＝**事前伝播**）。各ノードはこの小さく正規化された共有ブリーフに
個別準拠すればよく、集約ノードが全出力を読み直す必要がない。

- **正本 `rules.md` との関係**: `rules.md` は人が書く**恒久**ルール（全タスク常時）。run ブリーフは
  その一段手前——**タスク/ブランチ・スコープで一時・自動蓄積・追記のみ**——の層で、成果が done/マージ
  したら役目を終える（一般化できる項目は learn→rules 昇格で正本へ格上げ）。置き場所も `rules.md` と
  同じ `<root>` 直下に並べ「正本 `rules.md` ↔ 一時 `brief/`」の対比を明確にする。
- **なぜブリーフか**: `feedback` フィールドは差し戻しのたびに上書きされ過去の指摘が消える。`rules.md`
  は hit 閾値の昇格を要し即時には効かない。ブリーフは両者の隙間を埋め、ブランチと同じキーなので
  **リトライ（新 run-id）でも指摘がブランチと一緒に引き継がれる**。
- **蓄積の入口**: 検収差し戻し（needs feedback）・`revise`・gitlab 却下コメント・cohort 波及（兄弟へ横展開）。
  いずれも正規化・重複排除して追記する（冪等・決定的）。
- **ノード発見制約の環流**: 各ノードは「他ノードも従うべき恒常的な制約・規約」（命名・配置・様式・前提の
  統一など）を発見したら、最終成果に機械可読な JSON `{"constraints": ["…"]}` を添えて提示する。
  agent-project は run 終了時に `agent-flow result --json` から回収し run ブリーフへ環流する（次 run 以降の全ノードへ伝播）。
- **無効化**: `--no-task-branch`（ブリーフのライフサイクルは task_branch に連動）。ブリーフファイルは人が編集・削除してよい。

### 共有前 redaction 契約

**結論**: agent-project は、資格情報、認証 token、ホームディレクトリの実パス、生プロンプト、
ラベル付き金額（`amount=` / `$` / `¥`）を共有禁止とする。
`brief/` と `decisions/` では追記前に置換し、状態リポジトリでは commit/push 対象のスナップショットを再検査する。
どちらかを通過できなければ共有処理を止める。ログには元の値や該当行を残さず、ファイルパスと検出区分だけを出す。
置換には `[REDACTED:TOKEN]`、`[REDACTED:HOME]`、`[REDACTED:PROMPT]`、`[REDACTED:CREDENTIAL]`、
`[REDACTED:AMOUNT]` を使う。task 原価の `usd=` / `@cost` 記帳は現行共有経路が使うため AMOUNT 対象外。

| 検査境界 | 対象 | 失敗時 |
|---|---|---|
| `brief/<id>.md` / `decisions/<id>.md` の追記直前 | feedback、reason、learn、ノード発見制約など、外部入力を含む追記本文 | 追記しない。呼び出し元へ失敗を返す |
| state git の commit/push 直前 | 同期除外を適用した後の共有スナップショット全体。`brief/` と `decisions/` も再検査する | commit/push しない。同期を失敗として終了する |

置換後は同じ検査をもう一度行う。禁止値が残る、ファイルを読めない、検査器が例外で終了する、または安全に
置換できない場合は fail-closed とする。既存履歴は自動で書き換えない。過去の漏出を検出した場合も同期を止め、
履歴の除去は人が別作業で行う。

プライバシー fixture には、実在しない token（埋め込み・Bearer 含む）、POSIX/macOS/Windows のホームパス、
生プロンプト（英/日）、生の資格情報、ラベル付き金額（`amount=` / `$`）を区別できる sentinel として置く。
契約テストは各 sentinel を `redact_for_share` 本体・`brief/` / `decisions/` の入口から流し、共有候補の
全ファイルに元値が無く、無害な本文（相対パス・公開 URL・既存 `usd=` 原価記帳を含む）は残ることを確認する。
さらに禁止値を共有スナップショットへ直接混入させ、state git が非ゼロで終了し commit/push しないことを固定する。
残渣・検査器例外は fail-closed。実在の秘密は fixture、失敗メッセージ、テスト成果物に使わない。

このテストは既存の `tools/agent-project/tests` に置く。GitHub Actions の agent-project unittest ジョブが pull request と
`main` への push で同ディレクトリを全件実行するため、redaction の失敗は CI 失敗になる。保証範囲は fixture が通る
書き込み経路と state git の共有境界までで、外部ツールが直接 push する経路、CI を迂回した push、既存 Git 履歴の
消去までは保証しない。

state git だけで検査する案は却下した。漏出した本文が commit 前でも次の run に注入されるためだ。CI だけで検査する案も、
リモートへ送った後にしか失敗を検出できない。追記前の置換と共有直前の再検査を同じ契約にする。

### フレーク耐性 / 回帰 / 検収 / パス保護

- **フレーク耐性** `--verify-confirm N`（既定 1）: verify を最大 N 回再実行し PASS/FAIL が跨いだら **flake** と判定して
  自動修正せず人へ隔離（retry を増やさない）。揺れる verify の NG churn や flaky PASS の偽 done を防ぐ。
- **回帰ゲート** `--regression-cmd "<cmd>"`: verify PASS 後・done 確定前に共通検査を走らせ、失敗したら done にせず
  人へ。`--regression-revert` は未コミットの作業ツリー変更のみ best-effort で戻す（既定 off）。
- **検収ゲート**（verify=PASS でも人の承認）: タスク `- review: human` か policy `gate: <パターン>`。対象は archive せず
  `review`（検収待ち）になり `needs/<id>.md` を生成。`approve <id>` で done 確定／フィードバックで差し戻し。
- **パス保護**（safety denylist）: policy `protect: <glob>` に一致するファイルを act が**変更したら** verify=PASS でも
  done せず検収待ちへ。`gate` がタスク一致なのに対し `protect` は**変更されたパス**一致。
- **プロジェクト共通チェック**: state repo が持つ1本のコマンドを `regression_cmd` に設定する。
  agent-project はコマンドの中身や使用する検証 CLI を解釈せず、各タスクの verify PASS 後、done 確定前に
  実行する。失敗時は done にせず人へ戻す。

  ```yaml
  regression_cmd: ./tools/check
  ```

  `tools/check` の中で、テスト、[`codd-gate`](../codd-gate/README.md)、その他の検証 CLI を順に呼ぶ。
  検証 CLI を増やすときに変更するのはこのファイルだけでよく、agent-project の設定や通常タスクの
  `verify` は増やさない。既存負債を自動で backlog へ投入する必要が明確な場合だけ、別途 `intake_cmd` を使う。

### policy.md（人による上書き・per-project）

```yaml
deny:    prod        # "prod" を含むタスクは自動実行しない（実行前に止める）
pin:     T3          # 最優先 ／ defer: cleanup（後回し）
offload: heavy       # 分散環境へ移譲（--git-bus 設定時）
gate:    release     # verify PASS でも done 前に人の承認（検収ゲート・タスク一致）
protect: auth/**     # act が触ったら done せず承認へ（パス一致。glob: *=非/ **=/含む・**/ は0階層可）
route:   API -> app  # タスク（id/タイトル一致）の書込先ワークスペースを charter の repo 名へ割り当てる
```

`deny` は**実行前**で止め、`gate`/`protect` は**実行・verify は通すが done 確定前**で止める（止める位置が違う）。
無人運用の推奨デニーリスト: `.env` / `.env.*` / `**/secrets/**` / `**/credentials/**` / `**/*_key*` /
`**/migrations/**` / `auth/**` / `payments/**` / `k8s/production/**`。
> 変更検出は workdir の git（未コミット＋act 後差分）で best-effort。remote/daemon オフロードは workdir に差分が
> 出ないため対象外（実行先側で守る）。

## 収束と予算（必ず止まる）

| 停止理由 | 意味 | フラグ |
|----------|------|--------|
| `drained` | 消化可能タスクが尽きた | — |
| `budget` | サイクル数 / 実時間が尽きた | `--max-cycles`(20) / `--max-seconds`(0=無制限) |
| `cost` | トークン / 金額が尽きた | `--max-tokens` / `--max-cost`（0=無制限） |
| `throttle` | ソフト予算比率超過（watch は report 降格で spend を止め監視継続） | `--throttle`（例 0.8） |
| `infrastructure` | 所有権など基盤状態を確認できず安全停止した | — |
| `report` | report モードで計画だけを出した | `--level report` |
| `once` | 1 タスク実行後に停止した | `--once` |

- **コスト計上**は act 出力の `@cost tokens=… usd=…` 行を加算（決定的・吐かなければ 0）。done 時に納品書へ `- cost:`
  を残すので `stats` が累計を出す。検証 NG は `--max-retries`（既定 2）超で人へ。
- **レーン減速** `--pace <秒>` で 1 サイクルの下限間隔。`--max-seconds` 併用で `max_seconds/max_cycles` に均す。

**終了コード（非 watch 時）**: `0`＝drained かつ人の対応待ち無し ／ `1`＝人の対応待ち（blocked/review）あり ／
`2`＝その他の停止（budget / cost / throttle / infrastructure / once）。

## 自律度（信頼を段階的に明け渡す）

| level | act | done | 用途 |
|-------|-----|------|------|
| `report` | しない | — | 「何を・どの順で回すか」だけ報告（消化せず計画を出す安全な下見） |
| `assisted` | する | 人が `approve`（全件 review） | 実行するが done は必ず人が承認 |
| `unattended`（既定） | する | 自動（ゲート通過時） | protect/gate/regression を通れば自動 done |

- **タスク単位の上書き**: タスク行 `- level: …`。実効 = `- level:`（明示）＞ track の自動昇格 ＞ グローバル `--level`。
  `protect`/`gate`/`regression` は level に依らず常に上乗せ。`report` のタスクは実行せず計画に保留。
- **実績連動の自動昇格（opt-in）** `--auto-level` ＋ `- track: <名前>`: 同種群の手戻り率が低ければ level を自動で 1 段
  上げ、手戻り（差し戻し/回帰/偽done）で下げる。ceiling 既定 `assisted`（`--auto-level-max unattended` で完全無人化を
  解禁）。track 状態は `<root>/autonomy/<track>.json`、遷移は `decisions/` に監査記録。
- **適性の採点** `audit`: backlog/policy/config/state から決定的に L0–L3 を採点（スコア・赤旗・提案）。`audit --strict` は
  スコア<40 か critical 赤旗で exit 2（CI ゲート）。L3 は verify 健全＋コスト予算＋保護デニーリスト＋掃除が揃うときのみ。

```bash
agent-project run --level report                 # 計画だけ（act しない）
agent-project run --level assisted               # 実行するが done は approve 待ち
agent-project run --auto-level --auto-level-max unattended   # 実績で自動昇格
agent-project audit --strict                     # 無人運用に値するかの門番
```

## 人の判断とフィードバック

タスクが人の判断へ回ると案件毎 `needs/<id>.md` が生成される。

- **実行前レビュー（plan_review・既定 on）**: 新規タスクはすべて `proposed` で入り、**人の承認を通る
  まで実行されない**。needs/<id>.md（実行前レビュー票・タスク定義つき）で三値の決着ができる:
  - **承認** … `approve <id>`（または票を空のまま `[x]`）→ ready になり実行対象へ
  - **差し戻し** … 票に修正指示を書いて `[x]` → agent-project がタスク定義を修正して**再提案**（再び proposed）
  - **却下** … `reject <id> --reason ...` → 廃止（archive へ退避・avoid 記録・墓標）。**依存先（after 逆辺・推移）は
    proposed に戻して再審査**にかける。再計画は要求しない（分解は人の明示操作）——却下済みは
    次の分解時にプランナーへ「意図の似た再提案も抑止」として渡る
  従来の自動投入（verify ありは即 ready）へは `--no-plan-review`／設定 `plan_review: false` で戻せる。
- **影響範囲の一覧（impact）**: `agent-project impact <id>` で前提（after 上流）と依存先（下流・推移）を
  一覧表示。revise / reject 時にも影響先が出力・DR に添えられる。

- **承認 = 完了か、積み直しか**: `approve <id>` は既定で「ブロックを解いて積み直す」。
  成果を受け入れて**完了（done 確定）にする**ときは `approve <id> --complete`
  （commands ドロップなら `{"command":"approve","complete":true}`）を使う。
  以前は承認理由の文面から完了意図を推定していたが、推定が外れると黙って積み直され、
  同じ工程を再実行してまた要対応に戻る往復になっていた。**意図は呼び出し側が明示する**。
  agent-dashboard の「承認して完了にする」はこの `complete` を送る。

- **決着カード — 検証が決着しないときに人が選ぶ 4 つの道**: 判定エージェントが時間切れになる、
  そのモデルでは基準を確かめ切れない、といった理由で検証が決着しない票には、材料
  （どの基準が決着しなかったか／何で・どれだけ待って確かめたか／同じ理由が何回続いたか）と
  **4 つの出口**が載る。dashboard の要対応カードにはそのままボタンとして出る。
  | 出口 | 何をするか | 実体 |
  |---|---|---|
  | 条件を変えて再検証 | 成果物は作り直さず、判定に使う CLI・モデル・待ち時間だけ変える | `revise <id> --verify-agent codex` |
  | 受入基準を書き直す | 要求そのものを下げる（何を諦めたか基準に残る） | `revise <id> --acceptance ...` |
  | 止めて他を進める | 保留。環境を直したら承認で戻す | `hold <id>` |
  | 未検証で締める | 検証せずに完了。統合しない | `force-complete <id>` |

  **出口はこの 4 つで固定**し、失敗の種類やエージェントの種類では増やさない（それらは材料で
  あって出口ではない）。判定を緩めて通す設定は無い——品質を落とす正直な表現は「要求を下げる」か
  「未検証と明示して締める」の 2 つだけで、ゲートを黙って緩めるのは品質が落ちた事実を記録から
  消すことにしかならない。詳細は
  [検証の決着](../../docs/plans/2026-08-09-verification-settlement-design.md)。

- **強制完了（force-complete）— 進まないタスクを終わりにする**: `force-complete <id> --reason ...`
  は「done は verify のみが根拠」の**唯一の例外**で、人がタスクを終わりにするための最後の口。
  承認（`--complete`）は検収待ち（`review` / 成果のある `blocked`）にしか効かないため、
  `doing`（実行中）・`offloaded`（委譲中）・`ready` で堂々巡りしているタスクはどの操作でも
  done にできず、承認しても ready へ積み直されて同じ工程がまた止まる往復になっていた。
  通常の完了と決定的に違うのは 3 点で、記録の上で必ず見分けられるようにしてある:
  - **verify を実行しない**（未実施のまま完了にする）
  - **成果ブランチの自動統合をしない**（検証していない変更をターゲットへ入れない。
    統合が要るなら人が MR を見て決める）
  - 納品書（`archive/<id>.md` の `- 検収 : FORCED` と `verify … → 未実施`）・受領書
    （`DELIVERY.md` の検収欄）・決定記録（`action: force-complete`）に**未検証として残る**

  理由は必須。実行中・委譲中なら先に run を切り離してから確定し、遅れて戻ってきた試行の
  結果は採用しない（採用するとタスクが backlog へ復活し、archive と二重在庫になる）。
  track の実績には**手戻りとして**記録する（検証を通していないので信頼を上げない）。
  **やめる（作り直させない）なら `reject`、終わりにするなら `force-complete`**。

- **フィードバック往復**: 「## Decision Outcome」欄（MADR 互換。旧「## フィードバック」も可）に方針を書き `- [ ] 確定` を `- [x]` にして保存すると、次パスで拾われ
  ブロック解除＋内容を次 act に反映し `decisions/<id>.md` に記録。**誤発火防止**は ①チェックボックス `[x]`（空でも「そのまま
  再実行」）②`status: draft`（消化対象外）③`--debounce`（既定 3 秒）。
- **決定記録（DR）**: 人の判断は承認操作と不可分に `decisions/<id>.md` へ append-only。`approve`（修正承認）/
  `hold`（policy deny 追加）/ `reprioritize --pin|--defer`。DR の `- learn:` 行が下記の学習材料になる。
- **判断の自動抽出（learn/avoid・既定 on）** `--learn-capture`: 承認/保留の**理由をそのまま横断知識に蓄積**する。
  `approve`（差し戻し修正・検収承認いずれも）の理由は `- learn:`（＝どう解けば良いか。DR 学習・ltm が使う）、
  `hold` の理由は `- avoid:`（＝この種は自動実行させない。下記リコールが使う）として残す。`--no-learn-capture` で
  抑止（DR の本文は従来どおり残る）。従来 `- learn:` は差し戻し系にしか付かず、承認・保留の判断は横断的に死蔵していた。
- **予防リコール（投入/triage の shift-left・既定 on）** `--intake-recall`: `enqueue`／`triage` の時点で、新規タスクが
  過去の `hold`（`- avoid:`）とタイトル類似（Jaccard ≥ `--learn-threshold`）なら、**ready にせず実行前に人の判断へ回す**
  （`blocked`＋`needs/<id>.md`。verify を持つタスクでも triage の inbox→ready 自動昇格に呑まれない）。人は `approve` で
  実行許可／`hold` で恒久デニー化。DR 学習が「一度失敗してから」人を絞るのに対し、これは**投入の時点で先回りして止める**。
  `--no-intake-recall` で無効。決定的なファイル走査＋Jaccard のみ（エージェント不要）。
- **能動フィードバック（revise）**: needs はループが人へ回した時の**受動**の口。対して `revise` は、
  人が気づいた時点で**能動的に**タスクを修正し指示を届ける口（例: LLM がローカルサーバで e2e を
  始めたのに気づいた →「実サーバに配備して実施」へ即座に軌道修正）。
  `revise <id> [--title|--priority|--verify|--accept|--after|--note|--level|--track|--why|--desc|--scope|--out-of-scope|--constraints|--hints|--demo] [--feedback 指示]`
  でフィールドを置換（`''`/`none` で削除。`after` の循環は拒否）し、`--feedback` は次の act に
  必ず反映される。効き方はタスクの状態で変わる:
  - `ready`/`inbox`/`draft` … 即時反映（次の選択・実行から効く。依存 `after`・優先度の変更もすぐ効く）
  - `blocked`/`review` … 反映して ready に積み直す（needs 記入＋`[x]` と同じ復帰。needs は消える）
  - `doing`（実行中） … 反映を予約し、**現在の試行の結果は確定しない**（verify も done もせず
    修正内容とフィードバックで積み直す）。daemon/remote 実行なら結果待ちも打ち切って早く回す。
  watch のパス途中でも取り込まれる（後続タスクの実行前に効く）。決定記録（DR `action: revise`）と
  `- learn:`（feedback がある場合）を残す。
- **指示のファイルドロップ（commands/）**: CLI を実行できない環境（ビュアーが Windows・本体が WSL 内、など）
  向けに、同じ指示を `<root>/commands/<name>.json`
  （`{"command": "approve|hold|pin|defer|revise|reject|force-complete", "id": "<task-id>", "reason": "..."}`。revise は加えて
  `title/priority/verify/accept/after/note/level/track/why/desc/scope/out_of_scope/constraints/hints/demo/feedback`
  キーを受ける）のドロップでも渡せる。
  run/watch が拾って **CLI と同一のロジック・同一の DR** で実行し、処理したファイルは消す
  （壊れた JSON・未知の指示は `.err` に退避して journal に記録）。**読める指示は watch 中でも即座に
  取り込む**。`--debounce` は読めなかったファイル（書きかけ）だけの再試行猶予で、猶予後もダメなら
  `.err` へ退避する。読める指示を先送りすると、承認を処理しないまま再評価するパスが生まれ、
  承認直後にマイルストーンが復活する。
- **バックログ分解の要求（`replan`）**: `{"command": "replan", "reason": "..."}`
  （**プロジェクト単位＝`id` 不要**）のドロップ、または CLI `agent-project replan --reason ...` で、
  charter からのバックログ分解を **次パスに一発だけ**要求できる（`.replan.request` マーカーを立て、
  DR を残す）。**分解はこの明示要求でしか走らない**——「消化可能タスクが無い」「charter が変わった」
  を契機とする自動分解は廃止した（人が削除・整理したバックログを次パスが黙って作り直すため）。
  初回の分解も、charter 編集後の反映も、エラー回復のやり直しも、すべてこの口で人が起こす。
  分解の冪等照合は **done 以外**（現行処理中のバックログ＋却下済み）と行う: 処理中タスクの二重投入や
  却下済み（人の明示判断）の復活はさせず、`archive/`（done）と類似のタスクだけ**やり直しとして再作成を
  許可**する（過去の完了実績が回復のための分解を弾かない）。charter が無い（backlog ループ）
  プロジェクトでは対象が無いため拒否。
- **自律裁定（needs の手前・既定 on）**: 人へ回す前に エージェント CLI が「ループ内で積み直して解けるか（requeue）／人が要るか
  （escalate）」を判断。requeue なら needs を作らず guidance を注入して再実行。例外・エージェント CLI 不在・意思決定/リスク絡みは
  必ず人へ。1 タスク `--adjudicate-max`（既定 1）回まで。`--no-auto-adjudicate` で無効化。
- **DR 学習（通知を減らす）**: 繰り返し NG で人へ回りそうな時、他案件の `learn` からタイトル類似（Jaccard ≥
  `--learn-threshold` 既定 0.5）の過去指示を探し、あれば blocked にせず反映して自動再実行（1 タスク 1 回）。
  > 順序は **DR 学習（決定的）→ 自律裁定（エージェント CLI）→ 人**の三段で人の判断を絞る。投入側では逆に
  > **予防リコール（決定的）**が過去 hold に似た案件を先回りで人へ回し、無駄な実行と手戻りを未然に防ぐ。
- **ltm 昇格（横断・LLM 不要）** `--ltm`: ある `learn` が `auto-resolve` で実際に効いた回数が `--promote-threshold`
  （既定 2）以上で `ltm-use` home（`$KIRO_LTM_HOME`→`~/.claude`）へ昇格。recall は「ローカル decisions → ltm home」の順で
  フォールバックし別プロジェクトでも効く。`promote` で手動昇格。

- **通知**: 人の対応待ちへの**遷移時だけ**要約を標準出力に出す（毎サイクルでは鳴らさない）。`--notify-cmd '<cmd>'` で
  teams-use / outlook-use / issue-mailbox 等へダイジェストをパイプできる。永続の対応窓口は `needs/<id>.md`。

```bash
agent-project needs                              # 何が判断待ち/検収待ちか
agent-project approve T12 --reason "テスト側を修正"
agent-project hold prod-deploy --reason "本番は手動"
# 実行中でも気づいた時点で軌道修正（現在の試行は確定せず、修正内容で積み直される）
agent-project revise e2e-test --feedback "ローカルサーバでなく実サーバに配備して e2e を実施すること"
agent-project revise deploy --after e2e-test --priority 5 --reason "e2e 完了後に回す"
```

## backlog の自走

- **取り込み口（enqueue / inbox）**: `enqueue` は CLI フラグ or stdin/JSON（1 件/配列）から投入。`<root>/inbox/` に
  置かれた `.json`/`.md` は run/watch が取り込み元ファイルを消す。**verify を持たない投入は必ず `inbox`**＝人の triage 行き。
  外部ソース（webhook/メール/issue 抽出）は薄いアダプタでここへ流し込む。
- **取り込みコマンド（intake_cmd）**: 外部の決定的ゲート/検出器を **watch の周期で pull** する汎用フック（push 型の
  inbox と対）。設定 `intake_cmd:`（CLI `--intake-cmd`）のコマンドをパス開始時と idle 中に `intake_interval`（既定
  600 秒・0 以下で毎回）で律速して実行し、stdout の enqueue --json 形式を**冪等に**取り込む（spec の `id` が現役
  backlog に居れば飛ばす＝同じ発見の重複投入を防ぐ）。exit≠0・非 JSON・タイムアウト
  （verify_timeout）は journal に残して無視（ループは殺さない）。**コマンドは単発・有界であること**（常駐はこちらが
  持つ）。例: `intake_cmd: codd-gate tasks --debt`（doc/code/test 一貫性の負債を修復タスク化して自動返済）。
  > 外部 CLI を差し込める公式の口（verify/acceptance・regression_cmd・intake_cmd・inbox/enqueue・
  > notify_cmd・executor）の契約は設計書 §4.1「外部 CLI の差し込み点」にカタログ化してある。
- **依存（DAG）** `- after: T1, T2`: 依存が done（archive へ退避）になるまで消化対象に入らない。依存が blocked/review で
  止まれば従属も待つ。
- **自己生成（followup）**: 完了タスクから派生を生む。静的（タスクの `- followup: <title> :: <verify>`）／動的（act 出力の
  `@followup …` 行）。verify があれば `ready`（同 run で自走）、無ければ `inbox`。`--max-spawn`（既定 20）で上限。
- **rot 検知**: 古い/重複/実行不能を triage で検出し人へ回す（消さず棚卸し）。`rot [--fix]` 単体実行 ／ `run --rot` で毎回。

```bash
agent-project enqueue --title "レポート生成を直す" --verify 'pytest -q tests/report'
echo '{"title":"X","verify":"make test","priority":5,"after":"T1"}' | agent-project enqueue --json
cp task.md ./inbox/
```

## 複数バージョンの並行開発（charters/）

1 プロジェクトで複数バージョン（v1 保守と v2 開発など）を並行管理するには、`charter.md` の代わりに
**`charters/<バージョン名>.md`** を並べる。`run --watch` が全 charter をラウンドロビンで
plan→execute→evaluate し、それぞれが独立の acceptance / milestone（`needs/<プロジェクト>-<名前>.md`）/
収束状態（project.json の `charters` マップ）を持つ。

- plan が投入するタスクには `charter: <名前>` タグが付き、分解の重複排除・消化判定・評価は
  そのバージョンに閉じる（実行そのものは 1 つのバックログを共有）
- 特定バージョンだけ分解するには `agent-project replan --charter <名前>`（viewer の分解ボタンも同様）
- 単一 `charter.md` は従来どおり動く（charters/ が無いときのフォールバック）

### マスター憲章（`## master`）

ルートの `charter.md` に **`## master` セクション**を書くと、その憲章は「プロジェクト全体の
普遍的な前提（マスター）」になり、**それ自体はバックログへ分解されない**。やるべきことは
`charters/<名前>.md`（計画バージョン）に書き、そこからタスクが作られる。

- バージョンはマスターを**継承**する: goal / deliverables / acceptance はバージョン側が優先
  （空ならマスターの値を使う）、constraints / assumptions / links / repos はマスター∪バージョン
- マスターを編集すると継承合成後の内容が変わるため、各バージョンの accepted 再開の
  判定にもマスター編集が効く（分解への反映は人が `replan` を要求したときに行われる）
- バージョンが 1 つも無い間は分解対象なし＝backlog 消化と指示の取り込みだけが回り、
  `charters/<名前>.md` が置かれた時点で charter 駆動が始まる（`run --watch` が検知する）

## 目標駆動（charter）— `run` の charter モード（長期改善ループ）

backlog の上に、人が書く**目標（charter）**から逆算する evaluator-optimizer のもう一段。backlog を消化して
`drained` で止まる正準ループに対し、「**枯渇**」と「**目標達成**」を分離して長期に回す。**プロセスは `run` に一本化**され、
`<root>/charter.md` があれば `run` が自動でこの三相に入る（専用 `project` コマンドは廃止）。

```
charter.md（goal / constraints / assumptions / deliverables / acceptance=受入 verify ／ 任意 links）
   ① plan     charter をエージェントに分解させ enqueue（冪等。verify 必須）
              ＊人の明示要求（`replan` 指示・viewer の分解ボタン）があったときだけ起こす
                （自動分解はしない＝人が整理したバックログを勝手に作り直さない。既存/archive と冪等重複排除）
   ② execute  既存の正準ループ run を drained まで回す（検収/回帰/protect/予算は全て温存）
   ③ evaluate acceptance 全 PASS か判定（＋opt-in 敵対的レビュー --review-project）
        未達 → awaiting-plan（分解待ち）として milestone で人へ（改善タスクの自動起票はしない）
        全 PASS かつ改善ゼロ → milestone gate（needs/<project>.md）で人へ
```

- **done の唯一の根拠は `acceptance`（=verify）全 PASS**（タスク verify と同じ鉄則）。acceptance 無しの charter は
  done 判定不能＝必ず人へ。検証コマンドを書けない条件は **自然文でも可**（`- accept: …` か散文の箇条書き）。run 時に
  エージェントが決定的なシェル verify へ合成し（結果は安定キャッシュ＝done 基準がブレない）、合成できなければ人へ。
- **acceptance の実行先**: 既定は workdir だが、offload で worker が対象 repo を temp に clone・push して消すと workdir に
  成果が出ない。実行先は **明示 `--verify-cwd`（設定 `verify_cwd`）> 単一対象 repo の一時 clone（charter の非 readonly repo が
  1 つなら target ブランチを毎評価で `git clone --depth 1`）> workdir** の順で解決。clone 失敗は全 NG 扱い（成果の無い場所で
  偽判定しない）。複数 repo は曖昧なので自動 clone せず `--verify-cwd` で指定。
  **有限停止**: 内側 run ＋ `--max-project-cycles`（既定 5）/`--max-project-cost`/
  `--project-stall`（PASS 数が増えない連続回数で人へ・既定 2）。**知能は委譲**し enqueue・acceptance・収束は決定的。
- **収束候補は人へ**: `approve <project> --reason …` で完了確定（最終納品書）／charter を更新して次フェーズへ続行／
  policy・feedback で方向修正。`--watch` は milestone 提示後も常駐し charter 更新を待つ。状態は `<root>/project.json`、
  各評価は `decisions/` に `project-evaluate` で監査記録。
- **ワーカーへの定義/判断の注入**: agent-flow への act 依頼に **charter（定義）と `decisions/<id>.md`（判断結果）**を有界に
  注入（charter 1400 字・decisions 末尾 1000 字）。charter.md があれば全 act に乗る（無ければ空＝後方互換）。`## links` 先
  プロジェクトの定義＋判断（learn）も横展開で取り込む。
- **ワークスペース・ルーティング（repos レジストリの `owns:` ＋ policy `route:`）**: リポジトリ定義は
  独立スキーマ（`schemas/repos.schema.json`）で管理する。手書きの `<root>/repos.{yaml,yml,json}` が
  あれば**それがレジストリの正**（charter の `## repos` は互換入力で、内部的には同じ形に正規化して
  引き回す。charter 無しの backlog 消化でもルーティングに効く）。手書きが無ければ **charter から
  repos.json を自動生成**して外部ツール（codd-gate の `--repos` 等）へ渡す（`_meta` マーカー付き・
  正は charter のまま追従。手で管理したくなったら `_meta` を消す）。以下の `## repos` の説明は
  レジストリの内容の説明としてそのまま当てはまる。大規模・複数リポジトリ運用で「どのタスクを
  どのリポジトリへコミットするか」を**制御層（agent-project）が1つに決め**、agent-flow へ `--workspace`（唯一の書込先）として
  渡す。charter の `## repos` を repo レジストリとし、各 repo に `- owns:`（担当パスのグロブ）を付けると**書込先候補
  （ワークスペース）**になる。**owns を書かない repo は参照リポジトリ（読むだけ）**で、書込先にはせず agent-flow へ
  `--reference` で構造化伝搬する（clone しない。エージェントのプロンプトと gitlab イシューの参照節に描画される）。
  1 タスク（=1 agent-flow run）が書き込むのはちょうど 1 リポジトリ。複数 repo にまたがる変更は repo 別タスクへ
  分割し `after` で順序付ける。
  - **解決順（上が優先・決定はタスク md の `- workspace:`/`- routed_by:` に書き戻して安定/監査可能）**:
    1. タスクの `- workspace: <name>`（明示）  2. `policy.md` の `route: <パターン> -> <name>`（決定論）
    3. `owns:` のパスグロブ × タスクの `- paths:` ヒント（決定論推定）  4. auto-route（`route_planner: agent` のとき LLM が
    desc/owns から1つ推定）  5. `default_workspace` 設定 / 書込先候補が1つだけならそれ。
  - **リポジトリの同一性は (url, path, base)**：モノレポは「同じ url で path と owns を変えた複数エントリ」でフォルダ別の
    ワークスペースに、ブランチ別は base を変えて区別する。`path`/`base`/`target`/`desc` は構造化 `--workspace`（JSON）として
    agent-flow へ伝搬し、worker は `af/<run-id>` ブランチを base から作って作業、変更があれば agent-flow が commit/push する。
  - **verify の実行先もワークスペースに従う**: `- workspace:` を持つタスクは成果が workdir（git-bus ルート）でなく該当 repo の
    作業ブランチへ push されるため、verify/回帰を workdir で回すと「成果の無い場所」で偽 NG になる。そこで verify は**該当 repo を
    指定ブランチ（`target`→`base`）で取得し、`path` 指定があればそれをルートに**したクローン内で実行する
    （差分基準 `$AGENT_BASE_REV` はクローンの HEAD に取り直す）。取得は **URL 単位のホスト共有 bare ミラー
    （`--mirror --filter=blob:none`）から detached worktree を生やす**方式で、毎回 fetch してから最新で worktree を作るので
    都度 clone と鮮度は同等のまま GitLab の pack 生成負荷を抑える（ミラー root は `KIRO_GIT_CACHE_DIR`、既定
    `$TMPDIR/kiro-git-cache`、agent-flow と共有。詳細は
    [docs/designs/git-worktree-cache-pattern.md](../../docs/designs/git-worktree-cache-pattern.md)）。ミラーが使えなければ
    従来の `git clone --depth 1` に自動フォールバック。取得失敗・`path` 不在は黙って workdir に倒さず NG 扱い（成果の無い場所で
    偽判定しない）。明示 `--verify-cwd`（設定 `verify_cwd`）は常に最優先。
  - gitlab executor 経由なら**起票先プロジェクトをワークスペース URL から解決**し、フォルダ・作業ブランチ・参照リポジトリが
    イシュー本文に構造的に表現される。
- **cohort（pilot-then-batch）**: 「同じ手順を多数の対象に繰り返す」タスクを、**まず 1 件だけ走らせて指示を固めてから残りを
  生成・実行**する。`cohort_items` を持つ spec を投入すると、先頭要素が **pilot** として `review: human` 付きで 1 件だけ作られ、
  verify→検収ゲートで人が `approve`（必要なら feedback）して指示を固める。承認時にその定義を元に**残りのタスクを生成**し、
  各メンバには固めた指示（承認理由＋feedback）が `feedback` として乗って act に必ず反映される。`title`/`verify` 中の `{item}` に
  各対象が差し込まれる。状態は `cohorts/<id>.json`。**実行は act 非依存**＝残りは通常ループが任意の location（local/daemon/remote）
  で消化する。charter のプランナーも「繰り返しタスクは `cohort_items` でまとめよ」と指示され、分解から自然に cohort を作れる。
  手積みは `enqueue --title "{item} を移行" --verify "test -f {item}" --cohort-items a,b,c`。
  （人を介さない自動版＝「1件先行→自動検証→残り展開」は agent-flow の `exemplar_first` が担う。）
  選択肢としての when_to_use / when_not_to_use / 例示 / 適用具体例は flow-planner カタログの
  `variants.pilot-then-batch`（`.github/skills/flow-planner/patterns-catalog.yaml`）にまとめてある。

```bash
agent-project run                          # charter があれば plan→execute→evaluate（収束で人へ）
agent-project run --watch                  # 目標を満たすまで回り続ける常駐（charter 更新も待つ）
agent-project run --review-project         # acceptance 全 PASS でも短絡的達成を疑う
agent-project approve <project> --reason "受領"   # 完了確定（最終納品書）／続行は charter を更新して再実行
```

### バックログは「エージェントが書き、人が直す」（backlog-planner・S6）

分解は `.github/skills/backlog-planner/` のスキルが担う（設定 `planner_skill` で名前を変えられる。
見つからなければ組み込みプロンプトへ落ちる＝**計画は止めない**）。人の入力は charter とメモ書き程度で足りる。

```
人: charter を書く / notes/ にメモを書き溜める
  ↓  backlog-planner: バックログを全文記述（why / 作業概要 / 受入基準 / 規模感）
  ↓  proposed（計画レビュー）
人: レビュー票で直して承認 — 直したタスクは以後 replan で作り直されない
```

**必須項目**（`plan_sections: required`・既定）: `why`（なぜ）/ `desc`（作業概要＝変更対象・手順・影響範囲）/
`acceptance`（受入基準 3〜7 項目）/ `size`（S/M/L）。欠落は**機械で見て 1 回だけ再要求**し、
それでも欠けるタスクは**捨てずに人の目へ回す**（`plan_review` on なら proposed で票に欠落を書き、
off なら draft）。捨てると「プランナーが何も出さなかった」としか見えず、charter の書き方が悪いのか
スキルが壊れたのかを切り分ける材料が消える。`plan_sections: warn` で注記だけにもできる。

**重複を出させない**: プランナーの入力に既存タスク一覧と墓標を載せる。投入側の Jaccard 照合は
最終防衛線として残す（スキルは差し替え可能なので、投入側の護りは外さない）。

**墓標（`tombstones.md`）**: `reject` したタスクは 1 行ずつここに残り、**同じタイトルは再提案されない**。
人が手で書き足してもよい。agent-dashboard の「削除」もこの `reject` を投函する（タスク画面の
「却下済み（墓標）」から解除できる）。

```bash
agent-project revive "board の UI を作る"   # 墓標を解除（再び提案されうる状態へ戻す）
agent-project revive "board の UI を作る" --charter v1   # 複数 charter 運用では対象を絞る
agent-project replan --revive               # 今回の再分解だけ墓標を無視する（行は消さない）
```

**抑止は正規化タイトルの完全一致のみ**。類似（Jaccard）は投入を止めず、票に
「却下済みの『〜』に似ています（理由: 〜）」と注記するだけにする——抑止は取り返しがつかない
（黙って消えるので人は気づけない）が、提示は取り返しがつく（人が見て却下すればよい）。

**人が直した印**: `revise` かレビュー票の確定で `- edited: human` が付く。プランナーには
「人が確定済み・作り直すな」として届く。題を直しても原題（`- planned_title:`）が指紋として残るので、
次の replan で元の題のタスクが復活することはない。

**随時入力の整合パス**: `enqueue` / `inbox/` / `intake_cmd` から入るタスクは、既存タスクとの重複照合と
charter タグの付与を通ってから投入される。重複は新規作成せず「既存タスクに feedback を書いてください」を返す。

**毎パスの整合点（削除との整合）**: タスクの物理削除（viewer のゴミ箱移動・手作業・同期事故）で
生じた不整合は、各パスの入口でまとめて直す。要対応カードは status の投影として作り直し・掃除の
両方を行い（対応タスクの無い票は消える）、後続タスクの `after`（先行指定）から backlog にも
archive にも無い id を外し、タスク本体を失った付随状態（検証記録 `verifications/<id>/`・
run ブリーフ `brief/<id>.md`・実行権ロック）を物理削除する。却下（`reject`）はこれらの切り離しを
その場で自分から行う（依存先は再審査へ・run ブリーフは archive の却下記録へ転記して退役）。

### 観点メモ（notes/）— 書いても計画は動かない

```bash
mkdir -p notes && $EDITOR notes/2026-07-26.md    # 気になったことを書き溜める
agent-project distill-notes                       # ← 押したときだけタスク候補になる
```

`plan` は `notes/` を**自動では消費しない**。メモは「まだ決めていないこと」の置き場で、勝手に
タスク化されると人はメモを書けなくなる。`distill-notes` は notes をプランナー入力に載せ、
整合パス経由で proposed 投入し、取り込めたメモを `notes/archive/` へ移す（投入ゼロならメモは残す）。
dashboard のバックログタブ「メモ」ボタンからも同じことができる。

> CLI 名が `distill` でないのは、`distill_learn`（人コメント → 一般化ルールの蒸留）が既にその語彙を
> 使っているため（`agents:` の purpose キーにもある）。同名にすると設定でどちらか区別できない。

### spec は 3 段（スキップ / ライト / フル・S7）

`spec_track`（既定 off）を有効にすると、投入時採点 `max(c,r,a)`（各軸 1〜3）で spec 前段の要否を決める。

| 採点 | ルート | 成果物 | 展開 |
|---|---|---|---|
| `>= spec_threshold_full`（既定 3） | フル spec | `specs/<id>/` の spec.md / design.md / tasks.md | tasks.md を実装タスク群へ |
| `>= spec_threshold_light`（既定 2） | **ライト spec** | `specs/<id>/design.md` 1 枚 | しない（元タスクをそのまま実行し design.md を act の文脈へ注入） |
| それ未満 | スキップ | — | — |

ブラウンフィールドでは 3 点セットのオーバーヘッドが大きい。要求は charter とタスクの `why`/`desc` に
既にあり、分解は元タスクの粒度で足りる——ライト spec は「既存コードのどこをどう変えるか（変更方針・
影響範囲・受入条件の差分）」だけを書かせる。`policy.md` の `spec:` による強制はフルのまま。
旧 `spec_threshold` は `spec_threshold_full` の別名として読むので、既存設定はそのまま効く。

**既存コード文脈の前置**: 作業概要の「変更対象」もライト spec の「影響範囲」も、既存コードの文脈が
無ければ書けない。そこで plan と spec ルーティングの直前では `repo_map` 設定に関わらず
`context/<repo>.md` を用意する（HEAD sha キャッシュ済み・変化が無ければ 0 回。生成に失敗しても計画は進む）。

### 横展開リンク（charter.md の `## links`）

```markdown
## links
- ../shared-conventions   # 兄弟ディレクトリ（root の親からの相対でも解決される）
- /srv/projects/infra-rules   # 絶対パスも可
```

リンク先の定義（goal/constraints）と判断（decisions の `- learn:`）を act ワーカー文脈に取り込む（横断 recall・有界・
1 階層）。ltm-use（実績で自動昇格）に対し、charter リンクは**人が明示した参照先**を確実に引く。

## 複数プロジェクト（1 プロジェクト = 1 ディレクトリ = 1 プロセス）

複数プロジェクトはプロジェクトルートを並べて、それぞれで daemon を回す。needs/decisions/policy/journal/
検収ゲート/自律裁定/DR 学習は**そのルート内に閉じる**（別プロジェクトの判断が混ざらない）。束ねた可視化・
操作（検収・指示・停止/再開）は [agent-dashboard](../agent-dashboard/) が各ルートの clone を
登録して git 越しに行う。

```yaml
# ~/.agents/agent-project.host.yaml — この PC が持つプロジェクトの単一ソース
projects:
  - name: payments
    state_repo: git@gitlab.example.com:team/payments-state.git
    root: /home/me/agents/payments-state    # このノードでの clone 先（無ければ自動 clone）
  - name: webapp
    state_repo: git@gitlab.example.com:team/webapp-state.git
    root: /home/me/agents/webapp-state
```
```bash
agent-project serve      # 1 本の常駐体が両方を子プロセスとして監督する
agent-project status     # 心拍・子の生死・休止/切り離しを横断一覧
```

別プロジェクトの定義・判断を参照したいときは charter の `## links` にパス（兄弟ディレクトリ名や
相対/絶対パス）を書く（[横展開リンク](#横展開リンクchartermd-の--links)）。

## 状態の git 保存・共有 — リモートの viewer と結果/指示を往復する

ワークの内容（プロジェクトルート直下の状態＝backlog / needs / decisions / journal / DELIVERY / run-log …）を
**共有 git リポジトリへ双方向同期**する。リモートサーバで回している agent-project の結果を手元の
[agent-dashboard](../agent-dashboard/) で眺め、viewer からの指示（承認・フィードバック・タスク投入・
一時停止/停止）をサーバへ届ける、を git だけで往復できる。

### direct モード（推奨・設定不要）

**プロジェクトルート自体を共有リポジトリの clone にする**と、agent-project はそのリポジトリの
ブランチへ state コミットを積んで push し、viewer 側の commit（指示・検収）を取り込む。管理クローンは
作らない。ルートのチェックアウトには触れない: コミットは detached worktree（専用 index）で組み立てて
update-ref の CAS でブランチを進めるため、人の `git add`/`git commit` と衝突しない。

```bash
git clone git@example.com:team/proj-state.git ~/projects/proj
vi ~/projects/proj/charter.md
# host.yaml の projects に root を足して常駐体を起動（または再起動）する
agent-project serve
```

- リモートの取り込みは fetch → ff-only 優先・分岐時のみ rebase（--autostash 不使用＝未コミット変更と
  衝突するなら見送る）。push 競合は fetch + rebase → 再 push の指数バックオフで吸収し、**force push はしない**。
- `journal.md` は `.git/info/attributes` に `merge=union` を自動宣言（冪等・リポジトリローカル）。
  複数ホスト/viewer が同時に追記しても rebase で EOF 衝突せず、両方の行が残る。
- 同期対象はルート直下の状態のみ。一時状態（`bus/`・`claims/`）とドット始まりは同期しない。
- fetch/push は `state_git_interval`（既定 300 秒）で律速。push は共有すべきコミットがあるときだけ
  （run のパス直後は間隔を待たずに押し出す）。
- リモート（origin）が無いローカルだけの git リポジトリでも、コミット履歴として状態が残る（push はスキップ）。

### 共有先の指定

同期先は**状態ルート自身のリポジトリの `origin`**。状態ルートは常に状態専用リポジトリの
clone なので origin は必ずあり、**設定は要らない**。URL の宣言は host.yaml の
`projects[].state_repo` が唯一の置き場（clone を作る前に読める場所が要るため）。

```yaml
# 状態リポジトリ直下の agent-project.yaml（全 PC 共有）
state_git_interval: 300      # fetch/push の最短間隔（秒）。ローカルのコミットは毎同期で行う
```

同期先ブランチはルートが開いているブランチで、リポジトリ内のサブディレクトリ分離は使わない
（1 プロジェクト = 1 状態リポジトリ）。**ルートが無関係な既存リポジトリの内側にある構成では
同期しない**——そこで `git init` すると nested repo になり、外側の `git add -A` が壊れる。
その構成は起動時にエラーで止まる（状態が成果物リポジトリへ書き込まれる事故を防ぐ）。

- **双方向で、衝突は決定的に裁定**: 同時変更だけを **人の入力パス（`commands/`・`inbox/`・`needs/`・
  `policy.md`・`charter.md`・`repos.{json,yaml,yml}`）はリモート優先／機械状態（backlog・journal・
  decisions …）はローカル優先**の規則で決める。

同期は run のパス開始（指示の取り込み）・パス終了（結果の押し出し）・watch の idle（間隔律速の pull）で走る。
ネットワーク断・リポジトリ不通でも**ループは殺さず** journal に残して続行する（done の確定・消化は同期に
一切依存しない）。

**viewer 側（別マシン）の組み方**:

1. 状態リポジトリを clone する
2. viewer の ⚙ 設定「プロジェクトのパス」にその clone を登録する（複数プロジェクト = 複数 clone を 1 行ずつ）
3. viewer の操作（needs 記入・commands/ ドロップ・inbox/ 投入）はファイルとして書かれ commit/push される →
   サーバ側の agent-project が idle の pull で取り込み、watch が次パスを起こす

1台だけで動かしている間は、`claims/` は同一ホスト/共有FSだけの排他であり、Git越しの
多重実行防止にはならない。複数PCが同じbacklogを直接消化する場合は、次の Git CAS が自動で効く。

### 複数PCで1プロジェクトを分担（Git CAS）

全PCで同じ状態リポジトリを通常cloneする。**設定は要らない** — 状態ルートに origin があり、かつ
`status/<node>.json` に自分以外の生存ノードが観測されたときだけ Git CAS が自動で有効になる。
1台だけのうちは（origin があっても）取り合う相手がいないのでローカル実行のまま動き、
2台目が生存信号を書いた次のパスから CAS に入る。

> 以前あった `coordination: git-cas` 設定キーと `--coordination` フラグは廃止した。残っていても
> 無視され、起動時に警告が出る。「origin があるか」で判定していた頃は、単独PCでもリモートが
> 落ちているだけでタスクを1件も取得できなくなる問題があった。

調整したい場合のみ、共有 `agent-project.yaml` に以下を置く。

```yaml
controller_heartbeat_sec: 30
controller_lease_sec: 120
coordination_retries: 3
```

PC 固有値（`node_id` / clone 先 `root` / `availability` / ローカルクローン `repos[]` など）は
共有設定や環境変数へ置かず、各 PC の `~/.agents/agent-project.host.yaml` へ置く。サンプル:
[`agent-project.host.yaml.example`](agent-project.host.yaml.example)。共有設定へ書くと
起動時にエラーで止まる（→ [設定の 2 層](#設定の-2-層ノード固有-vs-プロジェクト共有)）。

```bash
agent-project doctor --project proj   # host.yaml の宣言から root/state_repo を解決する
agent-project serve   # 常駐体が host.yaml のプロジェクトを監督する（停止は Ctrl-C / systemd stop）
```

計画停止（毎晩の drain → 猶予 → 停止）は host.yaml の `availability` で宣言する。
子が自分で止まるのではなく常駐体が止める（[セットアップガイド](../../docs/guides/single-resident-setup.md) §2）。

- controllerはremote HEADへのfast-forward CASで1ノードだけが保持し、停止・drain・lease失効後は別ノードが自動取得する。
- controllerだけがcharter計画・inbox/commands/feedback・triage・自動割当を行う。workerは割当済みtaskだけを実行する。
- taskは `ready → doing` のCAS時に `claim_owner/token/generation` を確定する。古いtokenの結果は採用しない。
- 未割当readyはactiveノードのready+doing件数が最小になるよう配る。同数はノード名順。手動割当とdoingは動かさない。
- `daily_stop - drain_before_sec` で新規claimを止めcontrollerを解放する。異常停止したdoingは自動盗取せずblockedへ隔離する。
- Gitが取得不能ならcontroller取得・新規claimはfail closedする。`doctor` はノード名義、origin、heartbeat/leaseを検査する。
- `run-log/<node>/<run-id>.json` は不変レコード、`DELIVERY.md` はarchive集合から再構築可能。

**実行層 agent-flow のバス（run）も同じリポジトリへ**: バスの既定は `<root>/bus`＝状態の同期領域の内側
なので、agent-project 自身の状態同期がバスごと鏡写しする（agent-flow に第二の書き手を持たせない）。
バスを root の外に置いた構成でだけ、`--state-git` の routing を agent-flow へ注入する。agent-flow の
設定値（executor / state_git_subdir / gitlab.* / defer_waits 等）は `flow_config` で渡す
agent-flow.yaml に集約する。

## リモート操作（commands/ のライフサイクル指示）

viewer（または任意の外部ツール）は `commands/<name>.json` のドロップ → git push だけで、
タスク単位の指示（approve/hold/pin/defer/revise）に加えて**プロジェクト単位のライフサイクル操作**ができる:

| 指示 | 効果 |
|------|------|
| `{"command": "pause", "reason": "..."}` | watch の消化を一時停止（`paused.json` を生成。idle 監視・指示の取り込みは継続し、status.json に `paused: true` が載る） |
| `{"command": "resume"}` | 一時停止を解除して消化を再開 |
| `{"command": "stop"}` | プロセスを graceful 停止（停止前に状態を push。再開は常駐体が次の監視巡で子を起こす） |
| `{"command": "replan"}` | charter からのバックログ再分解を次パスに要求（エラー回復） |

pause 中も commands/ は取り込まれるため、リモートから resume / stop を届けられる。

### daemon の生存信号（status.json）— リモート viewer の稼働判定

リモート（別ホスト・state_git 越し）の viewer からは、本体のプロセスが直接見えないため、
従来「稼働中」バッジが出せなかった。
`<root>/status.json` に最小の生存スナップショット（`watch` / `level` / `updated_iso` /
`fresh_after_sec`）を書き、これも state_git で同期することで、リモートの viewer が
「同期経由の推定」として稼働判定・最終確認時刻を出せるようにしている。

```json
{"host": "myserver", "watch": true, "level": "unattended",
 "updated_iso": "2026-07-05T21:03:11", "fresh_after_sec": 600}
```

- **idle 中の追加コミットはデフォルトで発生しない**: `write_status` は実パス（backlog 等の実データが
  変わり得たタイミング）完了時にのみ呼ばれ、その他ファイルの変更と**同じコミットに相乗り**する
  （state_git の「差分があれば commit」に任せる。単体では何も追加しない）。watch の idle 中は
  `--status-interval`（既定 `0`＝無効）を明示指定しない限り status.json に一切触れない。
- **`--status-interval N`**（任意）: idle 中も N 秒間隔で status.json だけを更新し、実パスが
  長時間発生しない場合でも viewer 側で「生きている」ことを近い間隔で確認できるようにする。
  この間だけ state_git の追加コミットが増える（負荷とリモートでの鮮度のトレードオフ）。
  例: `--state-git-interval 300 --status-interval 3600` なら、実際の作業が無くても
  1 時間おきに 1 コミットだけ増える。
- `fresh_after_sec` は本体が自分の同期間隔（`state_git_interval` と `status_interval` の大きい方の
  2 倍・下限 120 秒）から計算して埋め込むため、**viewer 側は単純な経過時間比較だけで済む**
  （同期間隔を変えても viewer 側の調整は不要）。
- 実データ（backlog / needs / decisions / run-log 等）は既に state_git で同期されているため、
  status.json はそれらを重複させない（生存信号だけの最小ファイル）。

## 常駐運用（watch / lifecycle / 発見 / OS 自動起動）

- **watch**: 1 パスが終わってもプロセスを残し backlog を監視。idle 中は エージェント CLI/agent-flow を起動せず（安価な FS
  ポーリングのみ）、`--poll` 間隔で「消化可能タスク or 新規 inbox or フィードバック」を検知して次パスを起こす。
  予算は 1 パス毎に与え直す。サブコマンド省略（`agent-project`）は `run --watch` と同義（cwd のプロジェクトを常駐監視）。
- **常駐（serve / status）**: PC 単位の常駐体を 1 本立てる。`serve` は
  `agent-project.host.yaml` に宣言したプロジェクトを子プロセスとして起動・監視し、落ちたら
  再起動・繰り返し落ちたら切り離す。稼働時間帯（`availability`）の外では子を止め、時間が
  戻れば起こす。`status [--json]` が `~/.agents/engine/status.json` を読んで、心拍・tick 実績・
  同期の健康・子の生死（休止中／切り離しを別に）・実行中の仕事を出す。
  プロジェクト単位の一時停止・停止は commands/ の
  [ライフサイクル指示](#リモート操作commands-のライフサイクル指示)を使う。
  （プロジェクトごとに daemon を立てる `start` / `stop` / `restart` と稼働レジストリ
  `instances` は廃止した——常駐は PC に 1 本で、宣言の単一ソースは host.yaml。）

```bash
agent-project serve                  # 常駐体を起動（host.yaml のプロジェクトを監督）
agent-project status                 # 心拍・子の生死・休止/切り離し
agent-project status --json          # 機械可読
agent-project worker init            # プロジェクトを持たないワーカーノードの host.yaml を対話生成
```

**OS 自動起動**: `bash tools/agent-tools/install.sh --service` が systemd user unit を生成・有効化する
（`Type=notify` + `WatchdogSec` + `Restart=always` + `loginctl enable-linger`）。
Windows タスクスケジューラ方式との選択と手順は
[セットアップガイド](../../docs/guides/single-resident-setup.md) §4。

夜間停止は host.yaml の `availability` で宣言する（`daily_stop` / `drain_before_sec` /
`shutdown_grace_sec`）。drain 開始 → 停止時刻 → 猶予満了で子を止める、の 3 段で、止めるのは
常に常駐体。PC の電源管理は行わない（OS 側の shutdown/sleep スケジュールで管理する）。

## 設定ファイル

### 設定の 2 層（ノード固有 vs プロジェクト共有）

設定は 2 ファイルだけで、責務は「何を動かすか」と「どう動かすか」で分かれる。

| ファイル | 置き場所 | 責務 | 共有範囲 |
|---|---|---|---|
| `agent-project.host.yaml` | 各 PC の `~/.agents/` | **このノードの宣言**: 何を動かすか・資源・ローカル環境 | 共有しない |
| `agent-project.yaml` | **状態リポジトリの clone 直下** | **プロジェクトの合意**: どう動かすか | state repo で全 PC 共有 |

帰属は起動時に検査し、違反は理由と移行先を示して止める。黙って読み替えると、設定した本人が
「効いていない」ことに気付けないため。

- **host.yaml 専有**（プロジェクト yaml に書くとエラー）:
  `node_id` / `projects[]`（`name`・`state_repo`・`branch`・`root`・`overrides`）/ `repos[]` /
  `availability` / `budget` / `tags` / `board_workdir` / `amigos_bus` / `amigos_config` /
  `residency` / `defaults` / `update`。ノードごとに違う宣言なので、state repo 経由で全 PC へ
  配ると壊れる。
- **プロジェクト yaml 専有**（host.yaml の `defaults`/`overrides` に書くとエラー）:
  計画・ゲート系（`planner` / `flow_planner` / `granularity` / `plan_review` / `spec_track` …）、
  予算・収束系（`max_cycles` / `max_retries` / `env_resume_limit` / `level` …）、
  検証・学習・タスク運用系。
  ノードごとに食い違うと実行が非決定になる。
- **両方に書ける**（優先順位: **CLI > `projects[].overrides` > `defaults` > プロジェクト yaml > 既定**）:
  `agent_cli` / `model` / `act_timeout` / `verify_timeout` / `location` / `concurrency` /
  `agent_timeout` / `argv_limit` / `actor` / `notify_cmd` / `ltm_home` / `flow_config` / `verify_cwd`。
  導入済み CLI・マシン性能・ノード局所パスはノードごとに違ってよい。

```yaml
# ~/.agents/agent-project.host.yaml
node_id: pc-a
defaults:
  agent_cli: codex          # このノードの既定 CLI（全プロジェクト）
projects:
  - name: example
    state_repo: https://gitea.example/you/example-state.git
    root: /home/me/agents/example-state
    overrides:
      model: gpt-5.6-sol    # このノード × このプロジェクトだけの上書き
```

プロジェクト設定の探索は **状態ルート直下のみ**（`--config` 明示が最優先）。旧実装の
`cwd → ./.agents → ./.agent → ~/.agents` というチェーンは廃止した——移行時に成果物リポジトリ側の
古い yaml が黙って優先される事故が起きたため。旧探索先にファイルが残っていると起動時に
「読まれません」と名指しで警告する。

YAML は PyYAML 任意・無ければ JSON フォールバック（キーは同じ）。サンプルは
[`agent-project.yaml.example`](agent-project.yaml.example) と
[`agent-project.host.yaml.example`](agent-project.host.yaml.example)（実運用の組み方＝WSL 常駐＋
gitlab executor 分散＋viewer 監視＋GitLab バックアップは
[`agent-project.state-git.yaml.example`](agent-project.state-git.yaml.example)）。
スカラ＋真偽フラグ（三値 `--flag`/`--no-flag`）が対象で、
個別パス上書き（`--backlog` 等）・実行限定フラグ（`--json`/`--fix`/`--pin`）は CLI 専用。

### ノード固有のローカルクローン（`repos[]`・S3）

手元にある成果物リポジトリのクローンを host.yaml で宣言すると、ネットワーク越しのミラー
取り直しを省いてそこから worktree を切る（速い・オフラインでも動く）。

```yaml
# ~/.agents/agent-project.host.yaml
repos:
  - url: https://gitea.example/you/app.git
    local: /home/me/mirrors/app
```

**共有 repos.json には書けない**。あのファイルは charter から自動生成され、状態リポジトリ経由で
全 PC へ配られる——1 台で書いた絶対パスが全ノードへ伝播する。`local:` を書いても無視され、
起動時に移行先を示す警告が出る（`schemas/repos.schema.json` でも deprecated）。

宣言すると次のすべてに効く（読み手は `agentcore.repolocal` の 1 実装）:

- agent-project → agent-flow の run（`--workspace` に載せて渡す）
- 板（agent-board）で落札した仕事（請負ノードが自分の `local` を載せる）
- 検収差分の解決（成果物リポジトリのローカル解決）
- dashboard の CLIチャット起動先（そのフォルダを選んで開ける）

URL の一致は正規化して判定する（末尾 `.git`・スラッシュ・大小文字を吸収し、ローカルパス表記は
絶対化）。鮮度は従来どおり worker が毎回 `fetch` する（`local` は「どこから取るか」を変えるだけで
「取るかどうか」は変えない）。

### 状態ルートの起動契約

状態ルートは**常に状態専用リポジトリの clone**。旧 worktree 方式（`state_worktree_dir` /
`state_branch` / `state_commit` / `state_push` / `state_backup_branch`）は廃止し、これらのキーを
検出したら移行手順を示して止める。root の決め方は 3 通り:

```bash
agent-project run --project example        # host.yaml の projects[] から（常駐体の子もこれ）
agent-project run --state-repo <URL>       # 単発。root 未指定なら <cwd>/<リポジトリ名> へ clone
agent-project run --root <状態 clone>       # 手で clone 済みの状態リポジトリで直接
```

次の構成は起動時に止める（黙って旧構成・誤った場所へ書き続けないため）:

- 宣言した `state_repo` と root の `origin` が食い違う（旧 worktree への暗黙フォールバックは廃止）
- root が他の git リポジトリの内側（nested repo になり同期もできない）
- root が状態マーカーを持たない git リポジトリ＝成果物リポジトリらしい
  （移行前の `state_repo:` 入り yaml が残っていれば、その URL を案内に含める）

## 計測（stats / runlog）

```bash
agent-project stats [--json]     # 完了/納品/未消化/人対応待ち・自動化率・一発done率・累計コスト
agent-project runlog [--json --tail N]   # run 毎1行 JSON（reason/done/escalations/tokens/cost/duration）
```
`stats` は archive/decisions/DELIVERY/backlog から決定的に集計（**自動化率**=auto-resolve＋auto-adjudicate÷自動＋人、
**一発 done**=retry 0、コストは納品書 `- cost:` の累計で予算と突合）。`run-log.jsonl` は監視/スプレッドシートに流せる。

## 稼働診断（doctor）

```bash
agent-project doctor [--json]     # ログ/状態/環境から稼働を診断（既定は診断のみ・無害）
agent-project doctor --fix        # env/config を修正し、program の不具合を gitlab-idd で起票
```

`doctor` は **収集と適用を決定的に・診断と分類は エージェント CLI へ委譲** して稼働の問題を洗い出し、原因を 3 つに分類する。

- **env**（ユーザー環境固有）… エージェント CLI（既定 `kiro-cli`）/`agent-flow`/`git` の不在・PATH・workdir が git でない等。
- **config**（設定）… verify 欠落・コスト予算未設定・保護パス未設定・必須ディレクトリ未作成等（`audit` の未達も取り込む）。
- **program**（プログラム上の不具合）… 正しい環境・設定でも再現する不具合。**コード修正が必要なものだけ**。

材料は決定的チェック（依存コマンド・ディレクトリ・`audit` 結果）＋稼働シグナル（`stats`/`run-log`/`journal` 末尾/`needs`/
blocked タスク）。これを エージェント CLI に渡して分類済みの所見を得る（エージェント CLI 不在・解析不能なら**決定的チェックのみ**で続行）。

`--fix` のとき:
- **env/config** … 既知の修正アクションを適用（`create-dirs`＝backlog/needs/decisions 作成、`policy-protect`＝policy.md に
  既定の保護デニーリストを追記）。判断が要るもの（コスト予算・git 初期化等）は提案表示のみ。
- **program** … `gitlab-idd` スキルのリクエスター役（エージェント CLI 委譲）で **GitLab イシューを起票**。
  **スキルが見つからなければ起票せず出力のみ**（`$KIRO_SKILLS_HOME` → cwd 上方向の `.github/skills` → `~/.claude/skills` の順で探索）。

**実行層 agent-flow との連携**（`--with-flow`・既定 on／`--no-flow` で本体のみ）: 内側＝act の実体である `agent-flow doctor --json` を
同じバスに対して呼び、その所見を `[flow]` 印で統合する。`--fix` のときは agent-flow 側にも `--fix` を委譲し、**agent-flow が自分の
env/config 修正と program 起票を担う**（本体は agent-flow 由来の所見を再修正・再起票しない＝二重作業を避ける）。agent-flow が不在・
タイムアウト・解析不能なら無害にスキップする。

終了コード: `0`=健康（所見なし）／`1`=未解決の所見あり／`2`=未解決の critical あり。`--fix` 無しは常に診断のみ（既定）。

## 自動アップデート（既定 on）

スキルリポジトリ（このツールの配布元）の **main ブランチに更新が入ったら、`run --watch` のアイドル時に自動で取り込む**。
**既定で有効**（6 時間ごと。前回チェック時刻は `~/.agents/agent-project.update.json` に持続化され、
**再起動を跨いで間隔が尊重される**——前回から間隔ぶん経っていれば起動後の最初のアイドルで実施する）。
止めたいときは `update_enabled: false` か `update_check_interval: 0`。手順は doctor と同じ流儀で**決定的**——
知能は使わず、ファイル操作だけで完結する。

1. `git ls-remote` でスキルリポジトリ main の先頭コミットを確認する
2. 適用済み SHA（`~/.agents/agent-project.update.json`）と違えば「更新候補」
3. **アイドル時（消化待ち/フィードバックが無いとき）だけ**、temp 領域へ `tools/agent-project/` だけを **sparse-checkout**（無関係ファイルは取得しない）
4. **取得した本体の内容ダイジェストが前回適用時と同一なら適用せず、ベースライン SHA だけ進める**。
   direct state-git 構成では自分の状態同期の push でリポジトリの SHA が進むため、SHA だけで
   判定すると「自分の push → 更新検出 → 再起動 → また push」の自己増殖ループになる
5. その中の `install.sh` を実行して `~/.local/bin` の本体を更新する
6. **動いていたカレントディレクトリのまま** `os.execv` で新しい本体へ **graceful 再起動**する（レジストリ登録は再起動前に後始末）

再起動後の watch は **plan/act を始める前に状態 git を 1 回取り込む**（charter 駆動も同様）。
停止していた間に viewer が push した charter 更新・コマンド・フィードバックを、古いローカル状態のまま
計画してしまう前に反映するため（`run_loop` 入口の同期は plan の後になるので、再起動直後だけ先んじて import する）。

**更新元 URL は通常は設定不要**。`install.py` がインストール時に生成する `skill-registry.json`
（`~/.kiro` / `~/.claude` / `~/.copilot` / `~/.codex` のいずれか）の `repositories.origin.url`
（無ければ `install_dir` のローカルクローン）から自動解決する。別リポジトリを使うときだけ `update_repo` を明示する。

```bash
agent-project update --check    # 更新の有無だけ表示（取り込まない）
agent-project update --now      # 更新があれば install.sh を実行して再起動
```

設定ファイル（`~/.agents/agent-project.yaml`）で調整できる（すべて任意。**既定のままで有効**）。

```yaml
update_enabled: true                  # 自動アップデートの ON/OFF（false で完全に止める。既定 on）
update_check_interval: 21600          # 更新チェック間隔（秒）。既定 6 時間。0 以下で自動チェック無効
update_repo: ""                       # 空なら skill-registry.json から自動解決。別 repo を使うときだけ指定
update_branch: main                   # 追従するブランチ（空/既定なら registry の branch を採用）
update_subdir: tools/agent-project tools/agent-tools  # 取得対象パス（カンマ/空白区切りで複数）
update_installer: install.sh          # サブディレクトリ内で実行するインストーラ
```

> 初回チェックは「いま動いている本体が最新」とみなし、その時点の SHA をベースラインとして記録するだけ
> （更新はしない）。以降、main がそこから進んだときに更新を検出する。タスク実行中は何もしない。

## CLI 一覧

| コマンド | 役割 |
|----------|------|
| （省略）/ `run` [`--watch`] | 正準ループ（省略時は `run --watch`）。**charter.md があれば自動で目標駆動** |
| `triage` / `needs` / `rot` [`--fix`] | 優先順位付けのみ / 判断待ち表示 / rot 検出 |
| `enqueue` [`--title --verify\|--acceptance\|--verify-template …`\|`--json`] | 取り込み口（整合パスを通る: 重複照合・charter 帰属・墓標） |
| `approve <id>` / `hold <id>` / `reprioritize <id> --pin\|--defer` | 決定記録を残す人の操作 |
| `reject <id> --reason` | 却下（廃止・依存先を再審査へ・**墓標を残す**。次の分解で意図の似た再提案を抑止）。作業ブランチは `rejected/<id>` タグへ退避してから削除 |
| `mr-create <id>` | 検収待ちタスクの MR を作る（冪等・人の明示操作。旧名 `retry-mr`） |
| `force-complete <id> --reason` | 強制完了（**どうにも進まないタスクを人の判断で done 確定**）。verify は実行せず、成果ブランチの自動統合もしない。委譲中の run は切り離す。納品書・受領書に `FORCED`（未検証）として残る |
| `revive <タイトル>` [`--charter`/`--all`] | 墓標を解除（却下したタスクを再び提案されうる状態へ戻す）。墓標は `(指紋, charter)` 単位なので、`--charter <名前>` はその charter とタグ無しだけ、`--all` は全部を消す。未指定で対象が複数 charter に割れているときは、消さずに一覧を出す |
| `replan` [`--charter --revive`] | charter からバックログを分解（**分解はこの明示要求でしか走らない**。`--revive` は今回だけ墓標を無視） |
| `distill-notes` [`--charter`] | 観点メモ（notes/*.md）をバックログ候補へ分解（plan は自動では消費しない） |
| `impact <id>` [`--json`] | 依存関係（前提／依存先・推移）の一覧 |
| `stats` / `runlog` / `audit` [`--strict`] | 計測 / 構造化ログ / Loop Readiness 採点 |
| `doctor` [`--fix --json`] | 稼働診断（エージェント CLI）。env/config は修正・program は gitlab-idd で起票 |
| `update` [`--check --now`] | スキルリポジトリ(main)の更新を確認・取り込み再起動（[自動アップデート](#自動アップデートopt-in)） |
| `promote` | 効いた学習を ltm-use へ昇格（手動） |
| `serve` [`--host-config`] | PC 単位の常駐体を起動（host.yaml のプロジェクトを監督） |
| `status` [`--json`] | 常駐体の心拍・子の生死（休止中／切り離し）・同期の健康・実行中の仕事 |
| `worker` [`init`] | プロジェクトを持たないワーカーノードの起動 / host.yaml の対話生成 |

主なフラグ（抜粋）: `--root` `--planner{agent,none}` `--flow-planner` `--location{auto,local,daemon,remote,board}`
`--executor{agent,stub}` `--level` `--auto-level[-max]` `--max-cycles/-seconds/-tokens/-cost` `--throttle` `--pace`
`--concurrency` `--verify-confirm` `--require-progress` `--regression-cmd[-revert]` `--intake-cmd[-interval]`
`--auto-adjudicate` `--learn[-threshold]` `--learn-capture` `--intake-recall`
`--ltm[-home]` `--promote-threshold` `--rot[-age-days]` `--max-spawn` `--watch` `--poll` `--debounce` `--notify-cmd`
`--git-bus/-branch/-subdir` `--state-git[-branch/-subdir/-interval]` `--charter` `--review-project`
`--max-project-cycles/-cost` `--project-stall` `--dry-run` `--once`
`--planner-skill` `--plan-sections{required,warn}` `--spec-threshold-full/-light`。

## テスト

```bash
AGENT_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/agent-project/tests
```
agent-flow/エージェント CLI を呼ばずに検証（stub・act 注入）。優先順位/検証ゲート/積み直し/収束/location/pace/フィードバック往復/
watch/決定記録/コスト予算/followup・依存/回帰・パス保護/自己監査/自律度/原子的クレーム/run-log・throttle/flake/偽 done/
プロジェクト層/charter リンク/状態 git 同期（direct・管理クローン）/pause・resume・stop を網羅。agent-flow stub 統合は無ければ skip。
