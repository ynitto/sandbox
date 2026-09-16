# agent-app と agent-audit の責務分割、定型化した情報の共有と改善

作成: 2026-09-16。状態: **実装済み（画面を除く）**。裏側（agent-audit の口・申告・周期・共有・改善・IPC）は
2026-09-16 に入れた。画面は CLAUDE.md の規則により ASCII の承認待ち（§4.6）。

## 0. TL;DR

agent-audit を「読解して保存して判定する側」、agent-app を「起動して見せて、Windows 側の出来事を申告する側」に
分ける。両者は細い口 3 つ（台帳形の feed、`--json` の出力、監査ディレクトリ）だけで繋ぎ、agent-app に
集計や解析を写さない。定型化した情報（スキル、タスク、ワークフロー）の共有と改善は、git を持つ agent-app が
動かし、agent-audit は証跡と適格性を出すだけに留める。

主要な決定:

1. 監査ストアは Windows 側（agent-app の `userData/audit`）に置く。書き手は WSL の agent-audit 1 本、
   周期の主は agent-app。
2. WSL と Windows の境界を渡すものは、追記専用の台帳ファイルと単発の JSON 出力だけにする。再帰 glob と
   SQLite は境界を渡さない。
3. 共有先リポジトリへの push と改善タスクの起動は agent-app。agent-audit は allowlist 3 ファイルの外に
   書かない現行の不変条件を守る。

却下した主要案: agent-audit を Windows ネイティブで動かす（Python を配布物に同梱）、agent-app に読解と
集計を JS で書く、agent-audit に git push を持たせる、git-skill-manager を共有の実行者にする。

読むべき人: agent-app と agent-audit の両方に手を入れる人。画面だけを直す人は §4.6 だけでよい（画面の形は
CLAUDE.md の規則どおり別途 ASCII で承認を取る）。

## 1. 背景と課題

状況。agent-app は Windows で Electron として動き、CLI と tmux は常駐 1 本の `wsl.exe -e bash -l` の中で
動かしている（`src/main/host.js`）。会話の検索だけは agent-audit の `session_browser` を WSL の python3 で
起こし、Windows 側のホーム（`/mnt/c/Users/<me>`）も `extraHomes` として渡して読ませている。agent-audit は
Python 3.11 の zipapp で pip 依存が無く、POSIX に縛られる箇所は `cli-quota` の pty 読取だけ。保存は JSONL の
追記と JSON の原子置換、record の id は `source::store::native_id` の hash で決定的なので、同じ源泉を何度
読んでも重複しない。

問題は 3 つある。監査ストアが WSL の `~/.agents/audit` にあり、WSL の vhdx を圧迫し、WSL を作り直すと消える。
Windows 側の記録（VS Code の Copilot チャット、Windows ネイティブで動かした CLI、agent-app 自身の会話と
実行記録）は、WSL から `/mnt/c` を再帰 glob して読むしかなく、9p 越しの stat が数千回走って遅く、途中で
切れる。そして agent-app には集計も監査の画面も無く、agent-audit の `usage` や `report` は WSL の端末から
しか見えない。

問い。どちらが何を持てば、両方のデータを本人の作業を邪魔せずに集め、agent-app だけで見て回せるか。加えて、
会話から定型化したスキルやタスクを、本人の手間なしに指定リポジトリへ持ち寄り、実測に基づいて直し続ける
経路をどちらに持たせるか。

### 目標

- Windows 側ホームの取り込みを含めた `collect` 一巡が、本人の操作中でも体感で分からない負荷で終わる
  （目安: WSL 側 `nice 19`、1 巡 60 秒以内。超えたら §4.3 の棚卸し手渡しへ進む）。
- WSL を作り直しても監査ストアと洞察が残る。
- agent-app の画面から、監査の一巡の起動と結果の閲覧と共有先の設定ができ、WSL の端末を開かない。
- 定型化から共有先リポジトリへの提出まで、人の操作は merge の承認だけ。

### 非目標

- agent-audit の集計や抽出の規則を JS に写すこと。数字は必ず agent-audit が出す。
- 複数 PC の監査ストアを 1 つに合流させること（各 PC の agent-audit が自分の分だけ持つ。横断は共有先
  リポジトリの MR 本文に載る scrub 済みの証跡で足りる）。
- 共有先からの取り込み（pull）の新設計。スキルは git-skill-manager の pull、タスクとワークフローは既存の
  フォルダ追加で足りる。
- 監査画面の形。別文書で ASCII を出して承認を取る。

## 2. 主要な設計判断

### 2.1 読解と判定は agent-audit、起動と表示と申告は agent-app（JS への写しを却下）

判断: Windows 側のデータであっても、ファイルを読んで record に直す仕事は agent-audit だけが持つ。agent-app
は自分が知っている出来事（会話のターン、タスクの実行、共有の受託）を台帳形で書き出し、agent-audit を起こし、
出力を見せる。

文脈: agent-app は既に `audit-runtime` として agent-audit の 6 ファイルを同梱し、検索の読解を Python に
任せている（`package.json` の `build.extraResources`）。`sessionBrowser.js` は「索引側の判定を python の
`inspect()` と同義に保つ」と書いて写しの発生を避けている。

却下した案: (a) agent-app が Node で claude や codex の JSONL を解釈して集計する。readers.py の写しが
できて、CLI 側の形式変更のたびに両方直す。(b) agent-audit を Windows ネイティブで動かす。python-build-
standalone を exe に抱えるか、Windows に Python を要求するかのどちらかで、ADR-11 が避けた「更新の単位が
2 つ」を別の形で持ち込む。しかも WSL 側の源泉（budget 台帳、flow bus、project root）の方が量が多く、
`cli-quota` は pty で WSL にしか居られないので、ネイティブ化しても境界越えは消えない。

トレードオフ: 数字を見るには常に WSL の python が要る。WSL が落ちているとき画面に出せるのは、ストアに
ある洞察と決定とレポートの原文だけ（それは §2.2 でネイティブに読める）。確信度: 高。

### 2.2 監査ストアは Windows 側に置き、書き手は 1 本、周期は agent-app が持つ

判断: agent-app は `userData/audit` を `--audit-dir` で agent-audit に渡す。WSL 側の agent-loop hook
（`audit-calibrate-hook.py`）は agent-app の無い箱（Linux サーバ、headless の WSL）だけで有効にし、agent-app
の入った PC では agent-app の周期だけが動く。設計書の「一つの audit directory には同時に一つの書き手」を
そのまま守る。

文脈: 利用者が挙げた WSL の弱点は「境界が不安定」と「ディスクが逼迫する」。ストアの大半は transcripts の
写しで、これは agent-app の `session-index.db` と重複している。Windows 側は容量に余裕があり、WSL を消しても
残る。9p 越しで遅いのは stat と glob と SQLite であって、追記と rename ではない。

却下した案: (a) WSL に置いたまま gc を短くする。WSL を作り直した瞬間に洞察も消える。(b) 両側に持って
突合する。record id は決定的なので技術的には合流できるが、書き手が 2 本になり、設計書の不変条件を破る。
(c) agent-loop の hook を主にして agent-app は見るだけ。周期の主が agent-loop（任意ツール）になり、ADR-11 の
「無くても動く」に反する。

トレードオフ: `state.json`（この Mac で 3MB）を毎周期 9p 越しに rename する。実測で数秒に収まる見込み
だが、失敗したら周期を伸ばすのではなく `state.json` を cursors と seen に分けて小さくする。周期の主が
agent-app なので、agent-app を起動していない日は集まらない（cron の代わりは agent-app の常駐）。確信度: 中。
見直しの引き金は、cycle のログに 9p 由来の書き込み失敗が週 1 回以上出たとき。

### 2.3 境界を渡すのは台帳と単発 JSON だけ（glob と SQLite は渡さない）

判断: agent-app は自分の出来事を `userData/audit-feed/<YYYYMMDD>.jsonl` に node-budget の台帳行と同じ形で
追記し、agent-audit は既存の `budget-ledger` 読取（バイトオフセットのカーソル、壊れた行は読み飛ばす）で
取り込む。設定に台帳ディレクトリを複数書けるようにする以外、reader は足さない。Windows ネイティブの
transcripts は agent-audit の設定 `extra_homes`（`session_browser` の `extraHomes` と同じ概念）で
`/mnt/c/Users/<me>` を渡し、定義の `session_log.paths` の `~` をその home でも展開して読む。

文脈: agent-app の `share/ledger` は既に `{cli, model, seconds, tokens_in, tokens_out, status, error_class}`
で、台帳行とほぼ同形。`run-history` は 100 件で切れ、ledger は 30 日で cleanup が消すので、保存期間の正典を
agent-audit 側に移せる。会話のターンは CLI が WSL で動いている限り transcripts が `cli-native` で既に
集まるので、feed に載せるのはトークンではなく結び付け（会話 id、native session id、使ったスキル、
タスク id、リポジトリ）だけでよい。native session id は `~/.local/state/agent-app/cli-sessions/` に
既にあるので、agent-audit の相関を時間窓の推定から id の結合へ強くできる。

却下した案: (a) agent-app が transcripts を WSL へ複製する。ディスク逼迫を自分で作る。(b) 新しい source
kind `app-feed` を足す。台帳 reader で足りるのに種別を増やす。

棚卸しの手渡しは後回し: `/mnt/c` の再帰 glob が目標の 60 秒を超えたときだけ、agent-app が NTFS 上で
ネイティブに歩いた「変わったファイルの一覧（path, size, mtime）」を `collect --manifest` で渡し、
agent-audit は列挙を省いて読解だけをする。Kiro の SQLite は 9p 越しに開かず、agent-app が Windows 側で
一時ファイルに複製してから渡す。どちらも実測が出るまで作らない。確信度: 中。

### 2.4 定型化した情報の共有は agent-app が git で動かし、agent-audit は証跡と適格性だけ出す

判断: 共有先リポジトリ（agent-app の設定。`skill-registry.json` の書き込み可能なリポジトリがあれば初期値に
借りる）への提出は agent-app が行う。定型化（2026-09-13 の再利用設計の `routine`）で保存した成果物が
初めて成功したとき（タスクとワークフローは 1 回の成功実行、スキルは 1 回の会話利用）、agent-app が裏で
worktree を切り、成果物を正典の相対パス（`.github/skills/<名前>`、`.statemachine/<名前>/`、
`.agents/workflows/<名前>.yaml`）へ写し、`share/<種別>-<名前>` ブランチを push する。MR や PR の作成は
`gh` や `glab` がある環境でだけ行い、無ければ push したブランチの URL を受信箱に出す。個人リポジトリ向けに
「main へ直接」を設定で選べる。出所（元リポジトリ、コミット、会話 id）は成果物の隣の `origin.json` に
残す。

文脈: agent-app には `git.js` と `worktree.js` があり、LAN 共有の書き込み納品も「worktree `share-<id>` を
切って `share/<id>` を push」で設計済み。同じ道を使う。一方 agent-audit は「測る者に徹する」を掲げ、書ける
ファイルを `tuning.json`、`profiles.json`、budget の `config.json`（と `qualifications.json`）に限っている。
git の資格情報は Windows 側の本人にあり、WSL の zipapp から push させる理由が無い。

却下した案: (a) agent-audit に `share` サブコマンドを足す。allowlist の不変条件を破り、`report` が scrub を
通す前提も崩れる。(b) git-skill-manager の `push` を呼ぶ。Python のスキルで、skill_home が無い PC では
動かず、扱うのはスキルだけでタスクとワークフローを知らない。ただしリポジトリ内の置き場は registry の
`skill_root` に合わせ、pull 側の互換は保つ。(c) moltbook-use で共有する。SNS で知識の文章を回す道具で、
ディレクトリの成果物と git の履歴を運ぶ場所ではない。

トレードオフ: 共有先に「動いた」成果物しか出ないので、作ったが一度も回していない定義は出ない。それは
狙いどおりで、未使用の定義を共有先に積まない。確信度: 高。

### 2.5 改善は「適格性が落ちたら agent-app が改善タスクを裏で回して branch を出す」で閉じる

判断: agent-audit の `qualify` を成果物（種別と名前と出所）にも広げ、同じ評価 profile
（`min_samples 5`、`pass 0.8`、30 日窓）で `qualified` / `trial` / `blocked` を判定する。書き先は
**audit ストアの `artifacts.json`**（実装時の変更。当初は候補の `qualifications.json` に相乗りさせる
つもりだったが、あちらは Compiler が読む `(agent_cli, model, operation_class)` の契約で、`agent_cli` と
`model` を持たない成果物を混ぜると候補ゼロや偽の候補を焼く。ストア内に閉じれば外部への書き込み
allowlist も広げずに済む）。agent-app は周期の終わりに `qualify --json` の結果を読み、`blocked` か
`trial` に落ちた成果物について「証跡に基づいてこの定義を直す」タスクを既存の実行経路（AI 支援の読み取り
専用で案を作り、worktree で書く）で裏で回し、`improve/<種別>-<名前>` ブランチを共有先へ push する。本文には
`report --kind knowledge --json` から取った scrub 済みの観測（失敗クラス、再試行、verify の反転）を貼る。
merge は人。同じ成果物に対する改善ブランチが未 merge のうちは次を出さない。

文脈: 適格性の判定規則と閾値は agent-audit に既にあり、record には flow と project の run が task と node の
名前を運んでいる。足りないのは agent-app 経由の実行に成果物の参照を載せること（§2.3 の feed）だけ。

却下した案: (a) 改善案の文章を agent-audit の `distill` に書かせる。洞察は「何が起きたか」の集約で、
定義をどう直すかは実行経路と同じ CLI に書かせる方が短い。(b) 直した定義を自動で merge する。共有先は
他人も読む場所なので、人の承認を残す。(c) 使われていない成果物の退役提案。今回の範囲外にし、利用回数が
`usage --by ref` で見えるようになってから考える。

トレードオフ: 改善タスクは AI を 1 回以上呼ぶ。既定は `herd`（費用 0 のローカル LLM）が居ればそれ、
居なければ会話の「節約」tier と同じ CLI。走らせる時間帯は監査の周期と同じで、本人のターンが動いている間は
延期する。確信度: 中。実測で改善ブランチが merge されない比率が高ければ、閾値ではなく本文の証跡の質を
先に疑う。

## 3. 責務表

抽象度: コンポーネント。

| 仕事 | agent-app（Windows、Electron） | agent-audit（WSL、Python zipapp） |
|---|---|---|
| 周期 | 60 分の timer（`update.js` の Updater と同じ骨格）。単一飛行、ターン実行中は延期 | 持たない（app の無い箱では agent-loop hook か cron） |
| 収集 | 自分の出来事を `audit-feed` に台帳形で追記。Windows のホームを `extra_homes` で申告 | 全源泉の読解と正規化と保存（WSL 側の源泉、`/mnt/c` の台帳とホーム） |
| 保存 | `userData/audit` を提供。cleanup の KINDS に載せて容量を見せる | JSONL 追記と JSON 原子置換。gc |
| 集計と判定 | しない | `usage` `stats` `ratings` `qualify` `calibrate` `extract` `distill` `tune` |
| 表示 | `--json` を表に、`insights/` `decisions/` `reports/` はファイルを直接読む | Markdown レポートは従来どおり `reports/` へ |
| 共有 | 共有先リポジトリへ worktree と push、MR 作成、受信箱への通知 | しない |
| 改善 | 改善タスクの起動と branch の提出 | 成果物の適格性と証跡の出力 |
| 設定 | 有効、間隔、共有先、main 直接の可否、`extra_homes` | `agent-audit.yaml`（app は `--audit-dir` `--config` で上書き） |

## 4. 詳細

### 4.1 データの流れ

抽象度: 概要。

```
Windows                                   │ WSL
                                          │
 VS Code chat / native CLI ホーム ─────────┼─ extra_homes として glob（遅ければ manifest）
 agent-app                                │
   会話ターン・実行・共有 ──► audit-feed/*.jsonl ─┼─ budget-ledger 読取（offset）──┐
   userData/audit ◄───────────────────────┼── 書き手 1 本 ◄── agent-audit collect ◄─┤
   （records/observations/insights/…）     │        qualify / calibrate / extract     │ ~/.agents/budget
   画面 ◄── --json ◄──────────────────────┼── usage / stats / ratings / qualify     │ flow bus / project root
   共有先 repo ◄── worktree + push         │                                          │ cli-native transcripts
                                          │                                          └ cli-quota（pty）
```

境界を渡る矢印は 3 本だけ。台帳の追記（Windows から書き、WSL が offset で読む）、ストアの書き込み（WSL から
書き、Windows が読む）、`--json` の標準出力。再帰 glob は `extra_homes` の 1 本だけで、これが目標を超えたら
manifest に置き換える。

### 4.2 agent-audit に足す口

抽象度: 実装。どれも既存のサブコマンドの引数か設定の追加で、新しいサブコマンドは足さない。

- 設定 `ledger_dirs: [...]`。`budget-ledger` が `<budget_dir>/ledger` に加えてこれらも読む。カーソルの鍵は
  既にパスを含むので衝突しない。agent-app が渡すのは `audit-feed` の 1 本だけで、共有の台帳
  （`share/ledger`。行の形が違い `ts` を持たない）は直接読ませず、同じ行の形へ写して足す。
- 設定 `extra_homes: [...]`。`cli-native` が定義の `session_log.paths` の `~` をこれらの home でも展開する。
  Windows で見つからない home は黙って飛ばす。`doctor` は到達性を報告する。
- VS Code チャットの reader。`session_browser.py` の `vscode_session` を `readers.py` の format
  `vscode-chat` に昇格し、定義（`agents/copilot.json` の `session_log`）に並べる。読解の写しをここで 1 つに
  戻す。
- `qualify` に成果物を足す。record の `artifact: {kind, name, origin}` を集計鍵にして、audit ストアの
  `artifacts.json` へ書く（`qualify --json` が `observed_artifacts` と `artifact_changes` を返す）。
  種別の綴りは定型化（`shared/reuse.js`）と同じ `skill` / `task` / `workflow`——画面に出す言葉に
  内部の綴り（ステートマシン）を混ぜない。
- `collect --manifest <ndjson>`（実測後）。列挙を省いて渡された path を読む。

### 4.3 agent-app に足すもの

抽象度: 実装。画面は含めない。

- `audit.js`。Updater と同じ骨格の周期と、cleanup の `KINDS` と同じ表で持つ 6 段の連鎖
  （collect、qualify --apply、calibrate --write、extract、distill --review、tune --apply）。各段の終了コードを
  見て次へ進む判断は app が持つ（agent-audit の仕様が複合コマンドを置かない理由が「どこまで進んで止まったか
  の状態管理」なので、hook と同じ 6 行の表を app 側にも持つ写しを受け入れる）。WSL では `nice -n 19
  ionice -c 3` を前置し、`running` に会話ターンがあるか tmux の CLI が入力待ちでなければ次の tick へ延期する。
- `audit-feed` の書き手。ターン終了（会話 id、CLI、model、native session id、使ったスキル、リポジトリ）、
  タスクとワークフローの実行終了（`run-history` に書く record と同じ内容に `artifact` を足す）、共有の受託
  （既存 `share/ledger` の行をそのまま）を 1 行ずつ追記する。`run-history` と `share/ledger` は画面の
  ための短い写しとして残す。
- 共有先リポジトリへの提出（§2.4）と改善ブランチの提出（§2.5）。LAN 共有の書き込み納品と同じ worktree の
  道を使い、`origin.json` を成果物の隣に置く。
- IPC は `audit:status` `audit:run` `audit:summary` `audit:artifacts` の 4 つで、`cleanup:scan` と同じ
  `{ok, data|error}` の形。進捗は `audit:changed` の push。

### 4.4 共有と改善の手順

抽象度: 概要。

1. 会話から定型化して成果物をリポジトリに保存する（既存）。
2. 成果物が初めて成功する。agent-app が feed に `artifact` 付きの行を書き、同時に共有先へ `share/` ブランチを
   出す。受信箱に「共有先に出した」と 1 行。
3. 以後の実行は feed と flow と project の run record から agent-audit が集め、`qualify` が成果物の適格性を
   更新する。
4. `blocked` か `trial` に落ちたら、agent-app が改善タスクを裏で回し、`improve/` ブランチを出す。受信箱に
   「改善案を出した（証跡 n 件）」と 1 行。
5. 人が merge する。取り込みはスキルなら git-skill-manager の pull、タスクとワークフローなら既存の
   フォルダ追加。

### 4.5 性能への配慮

抽象度: 実装。

- LLM は既定で呼ばない（`agents.extract/distill` 未指定なら rules。2026-09-09 の実測どおり）。
- `with_transcripts` は agent-app の入った PC では既定 off。本文検索は `session-index.db` が持つ。
- 周期は単一飛行で、前回が終わっていなければ飛ばす。WSL 側は `nice` と `ionice`、Windows 側は Node の
  ファイル追記だけで重い処理を持たない。
- `extra_homes` の glob は `--since` で前回以降に絞る。それでも 60 秒を超えたら manifest（§2.3）。

### 4.6 画面に要る操作と、CLI に留めるもの

抽象度: 概要。形は別途 ASCII で承認を取る。

画面に出す操作は 4 つ。いま一巡を回す、集計と洞察を見る、改善案と共有の状態を見る、設定（有効、間隔、
共有先、main 直接）。`doctor` の結果は「足りないものの 1 行」として設定の下に出す。

CLI に留めるもの: `reconcile` `reclean` `gc` `seed` `sessions` `update` `trials` `tasks`。どれも運用者が
端末で使う口で、画面に 1 対 1 で並べない。

## 5. 検証と、先に測ること

実装時に通したもの（2026-09-16）。

- 両言語をまたぐ往復: agent-app が書いた 9 行（会話 3・実行 5・共有 1）を agent-audit が 9 件の record として
  収集し、`qualify --apply` が 5 回中 3 成功の成果物を `trial`（`failure_modes: [verify]`）と判定して
  `artifacts.json` を書いた。
- VS Code のチャット: この Mac の実データ 6 件を `vscode-chat` の reader が解釈し、`doctor` が
  「session_log あり（format=vscode-chat・到達可）」と出した。中身が空のチャットは未収集にする。
- テスト: agent-audit 238 件・agent-app 505 件（うち 2 件は既存の失敗で、変更前から落ちている
  macOS の一時パス（`/private/var`）と Electron の公開テスト）。

これから測ること。

- 実測 1: この Mac と Windows 機で `collect` を `extra_homes` 付きで回し、壁時計と 9p の stat 回数を取る。
  60 秒を超えるかで §2.3 の manifest を作るか決める。
- 実測 2: `userData/audit` を `--audit-dir` にして 1 周期回し、`state.json` の rename が失敗しないこと、
  周期の壁時計が WSL 内ストアと比べて何倍かを見る。
- 受入: (a) WSL を落とした状態で agent-app を起動し、直近の洞察とレポートが見える。(b) 定型化した
  タスクを 1 回成功させると、共有先に `share/` ブランチが出て人の操作が要らない。(c) 同じタスクを 5 回中 2 回
  失敗させると `improve/` ブランチが 1 本だけ出る。(d) `reconcile` が Windows ホームの session を missing に
  数えない。
- テスト: agent-audit は `ledger_dirs` と `extra_homes` と `vscode-chat` reader の単体。agent-app は
  `audit.js` の連鎖を偽 CLI で回す test と、feed の行の形を `budget-ledger` reader で読めることの往復。

## 6. 変えないもの

- agent-audit の「本体に複合コマンドを持たない」。連鎖の表は hook と app の 2 か所に置く。
- agent-audit の書き込み allowlist（`tuning.json` `profiles.json` budget `config.json` `qualifications.json`）。
- agent-app の ADR-11。agent-audit が無い PC では監査の項目が「任意」として薄く出るだけで本体は止まらない。
- 定型化の保存先を毎回本人が選ぶこと（2026-09-13 の決定）。共有先は保存先とは別の設定。

## 7. 関連

- [`agent-audit-design.md`](../designs/agent-audit-design.md)（常駐しない、書き手 1 本、allowlist）
- [`agent-app-design.md`](../designs/agent-app-design.md) の ADR-11
- [`2026-09-13-agent-app-reuse-design.md`](2026-09-13-agent-app-reuse-design.md)（定型化と保存先）
- [`2026-09-11-agent-app-shared-token-pool-design.md`](2026-09-11-agent-app-shared-token-pool-design.md)
  §5.2（worktree と push の道）
- [`2026-09-09-agent-audit-llm-free-extract-distill-assessment.md`](2026-09-09-agent-audit-llm-free-extract-distill-assessment.md)
