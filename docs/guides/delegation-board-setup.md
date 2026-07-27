# 委譲板セットアップガイド — run（タスク）単位の複数 PC 分散

タスクを**丸ごと 1 つの PC に請けさせる**分散（run 単位の先着分散）を始めるための
設定ガイド。実行グラフの中の個々のステップは PC 間に散らないのが仕様
（[複数 PC 分担運用ガイド](multi-pc-operations.md) §4.2）——このガイドがつくるのは
「タスクという単位を、能力の合う PC が早い者勝ちで請けていく」状態。

## 0. 板が要るか要らないかを先に決める

run 単位の分散には 2 つの経路があり、**板が要るのは 2 つ目だけ**:

| 経路 | 仕組み | 板 | 向いている場面 |
|---|---|---|---|
| A. 同一プロジェクトの分担 | 全 PC が同じ状態リポジトリのプロジェクトを宣言し、ready なタスクを早い者勝ちで取る | **不要** | どの PC も同じプロジェクトを担当でき、環境差が小さい |
| B. 委譲板 | タスクを板へ公示し、能力（担当リポジトリ・導入済み CLI・空き枠）の合う PC が入札・落札して丸ごと実行する | **必要** | PC ごとに能力が違う／プロジェクトを持たない請負専用 PC を増やしたい |

経路 A は [複数 PC 分担運用ガイド](multi-pc-operations.md) §3〜§4 がそのまま手順
（各 PC の `~/.agents/agent-project.host.yaml` に同じプロジェクトを宣言するだけ。板の設定は不要）。
以下は**経路 B（委譲板）**のセットアップ。

## 1. 全体像 — 誰が何を書くか

```
依頼側（プロジェクトを持つ PC）                請負側（請け手の PC。複数可）
  agent-project.yaml に board:                 host.yaml に board: と repos: を宣言
  policy.md に offload: パターン                 （プロジェクト 0 個の請負専用 PC でも可）
        │                                            │
        │ タスクを公示（post）                          │ 能力宣言・入札・落札・実行
        ▼                                            ▼
              板 = ただの git リポジトリ（処理を持たない）
              nodes/…      各 PC の能力宣言
              delegations/… 公示・入札・実行心拍・成果
```

- 板は **ただの git リポジトリ**。サーバもデーモンも増えない。公示・入札・落札・成果は
  すべて板の上のファイルで、各 PC が同じファイル集合から同じ結論を決定的に導く
  （詳細は [`tools/agent-board/README.md`](../../tools/agent-board/README.md)）。
- 公示から成果までの流れ: 依頼側が `delegations/<id>/post.json` を書く → 請負側の常駐体が
  巡回して入札 → 勝った PC が自分の実行エンジンへ取り込んで完走 → `result.json` を書き戻す →
  依頼側が回収してタスクを settle する。

## 2. 板の用意（1 回だけ）

専用の git リポジトリを 1 つ切る。置き場は普段の forge（Gitea / Forgejo / GitLab）でも、
ssh で見える bare リポジトリでもよい。

```bash
# 例: ssh bare リポジトリを板にする
git init --bare /srv/git/agent-board.git

# 例: forge に空リポジトリ agent-board を作るだけでもよい（README 等は不要）
```

決めごとは 3 つだけ:

- **全 PC から同じ URL で読める・書ける**こと（認証は普段の git 認証のまま）。
- **状態リポジトリとは別のリポジトリにする**。板はフリート（PC 群）に属し、
  プロジェクトには属さない——複数プロジェクトで 1 枚の板を共有してよい。
  迷ったらフリートに 1 枚。
- 中身は空でよい。ファイルレイアウトは各ツールが書きながら作る。

## 3. 依頼側の設定（プロジェクトを持つ PC）

### 3.1 `agent-project.yaml`（状態リポジトリ直下・全 PC 共通の合意）

```yaml
board: "git+ssh://git@forge.example/srv/agent-board.git"  # 板の場所
# board_workdir: /path/to/workdir   # git+ 板のクローン作業領域（省略 = 自動）
# board_workload: flow              # 公示する仕事の種類（既定 flow）
```

`board:` の値は 2 形式——ローカルディレクトリ（同一マシン内の板）か、`git+<URL>`
（リモートの板。URL は https / ssh どちらでも）。

### 3.2 `policy.md`（状態リポジトリ直下）— どのタスクを板へ出すか

```
offload: perf-*
offload: build-*
```

実行モードが既定（`location: auto`）のとき、**`offload:` パターンに一致したタスクだけ**が
板へ公示され、それ以外は従来どおりローカルで実行される。パターンはタスク id・タイトルに
対する glob。全タスクを板へ出したいなら `agent-project.yaml` に `location: board` を書く
（板が未設定ならローカルへ倒れる）。

### 3.3 手動で 1 件だけ出す（設定を変えずに試す）

```bash
agent-project board-offload <task-id> --board git+ssh://…/agent-board.git
```

dashboard の委譲タブからも公示・落札確定・中止を投函できる。

## 4. 請負側の設定（仕事を請ける PC）

### 4.1 プロジェクト 0 個の請負専用 PC（最小手順）

プロジェクトを 1 つも持たない PC でも、板の仕事を請けて実行できる（2026-07-27 対応）。

```bash
# 1. インストール（常駐一本化セットアップガイド §1 と同じ）
git clone <このリポジトリ> && cd <クローン先>
bash tools/agent-tools/install.sh

# 2. 請負専用の host.yaml を生成
agent-project worker init \
  --board "git+ssh://git@forge.example/srv/agent-board.git" \
  --agent-cli kiro                # この PC で使える CLI（複数ならカンマ区切り）

# 3. 生成された ~/.agents/agent-project.host.yaml に担当リポジトリを足す（重要・下記）

# 4. 起動（常駐化は常駐一本化セットアップガイド §4）
agent-project worker
```

**手順 3 を忘れると 1 件も入札しない。** 公示には対象リポジトリの URL
（`workspace.url`）が入っていて、**その URL を `repos:` に宣言している PC だけ**が
入札する（能力の無い PC が請けて壊すより、静かに見送る側に倒す設計）:

```yaml
repos:
  - url: https://forge.example/team/product.git      # 依頼側タスクの対象リポジトリ
    # local: /home/me/clones/product                 # 手元にクローンがあるなら（任意）
```

そのほか請負側の PC に必要なもの:

- 宣言した CLI（kiro 等）が実際に動くこと（ログイン済み・PATH が通っている）。
- 対象リポジトリを clone できる git 認証（`local:` を宣言すれば取得はそこから）。
- 引き受ける量の上限は `budget.max_concurrent`（省略 = 4 / `0` = 無制限）。板上の
  自分名義の預かり件数が上限に達すると、新しい公示に入札しない。

### 4.2 プロジェクトを持つ PC も請負側になる

フル構成の PC（`projects:` が 1 つ以上）が板の仕事も請ける場合:

- `~/.agents/agent-project.host.yaml` に `board:` を宣言する（能力の公開と入札・心拍が始まる）。
- 実行エンジン側の参加は、プロジェクトの実行エンジン設定（`agent-project.yaml` の
  `flow_config` が指すファイル）に `board:` を書いて開く。担当リポジトリ・タグ・CLI の
  宣言は host.yaml が正で、実行エンジン設定の `board_repos:` 等は明示上書き用。

依頼と請負は独立の設定なので、「出すだけの PC」「請けるだけの PC」「両方の PC」を
自由に混ぜられる。

## 5. 動作確認（最初の 1 件）

1. **能力宣言が板に出たか**: 請負側を起動して数分後、板リポジトリに
   `nodes/<自分の名義>.json` ができている（`tags` / `agent_cli` / `repos` / 空き枠が載る）。
2. **公示 → 落札**: 依頼側で `offload:` に一致する小さなタスクを 1 件 ready にする
   （または `board-offload` で手動投函）。板の `delegations/<id>/` に `post.json` →
   請負側の `bids/<名義>.json` → 実行中は `status/<名義>.json` が現れる。
3. **成果の回収**: 完走すると `result.json` が書かれ、依頼側の次の周回でタスクが
   settle される（成果は対象リポジトリのブランチ）。dashboard の板画面でも
   公示 → 入札 → 実行 → 成果の横断一覧を追える。
4. **入札されないとき**: まず請負側 PC で `agent-project doctor` を見る。
   よくある原因は順に——対象リポジトリが `repos:` に無い（手順 4.1-3）／公示が要求する
   CLI・タグを宣言していない／空き枠が無い（`budget.max_concurrent` に達している）／
   稼働時間帯の外（`availability`）。

## 6. run を小さく保つ（分散の効きを良くする）

板が配るのは**タスク単位**なので、1 タスクが巨大だと 1 台に長時間張り付き、分散の効きが
落ちる。タスクの粒度は依頼側の設定で制御する:

- バックログ分解の粒度 `granularity`（`coarse` / `fine` / `finest`）を 1 段細かくする。
- 大きな仕事は 1 タスクに詰めず、バックログの段階で分割する（分割の勘所は
  [中級ガイド](guide-intermediate.md)のバックログ運用を参照）。

どの PC がどれだけ実行したかは、実行エンジンの状態表示（PC 別の実行内訳）と dashboard の
run 詳細で確認できる——「1 台に偏っている」が見えたら、粒度を細かくするか請負側の PC を
足すのが正規の対処。

## 7. やってはいけないこと

- **板の中のファイルを手で編集しない**。板は各 PC が決定的に同じ結論を出すための
  データ契約で、手編集は落札の食い違いを生む。操作はすべて CLI / dashboard から。
- **同じ PC の名義を 2 つ作らない**（`node_id` の付け替えは
  [名義切替ガイド](node-id-cutover.md)の手順で）。板の預かり枠は名義単位で数えるため、
  名義が割れると枠の自己抑制が効かなくなる。
- **板を状態リポジトリと同居させない**。同期の負荷と権限の範囲が混ざり、
  どちらの不調か切り分けられなくなる。
