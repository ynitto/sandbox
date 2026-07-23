# 複数 PC 分担運用ガイド（agent-project + agent-dashboard）

> 対象構成: 「各 PC = Windows で agent-dashboard ＋ WSL で agent-project daemon」を
> 複数台並べ、1 つのプロジェクト（backlog）を分担して進める。
> **方針: 新しい同期機構は作らない。** 必要な仕組み（state-git 同期・`coordination: git-cas`・
> `commands/` / `needs/` メールボックス・PC 固有 profile）は既に実装済みであり、
> 本ガイドは「壊れやすい経路を避け、堅牢な経路だけを使う組み合わせ」を定義する。

## 前提（要件）

- 各 PC は Windows で agent-dashboard、WSL で agent-project を daemon（`start` = `run --watch`）で動かす。
- ユーザー操作は agent-dashboard 上で行い、処理は agent-project が行う。
- agent-dashboard は**見たい時だけ**起動する。agent-project は PC 起動時に起動するが、
  スケジュール（availability）やエラーで停止しうる。
- 人の判断・エージェント実行リソースは PC / 人ごとに分担する。**均等性は不要（ベストエフォート）**。
- データは**結果整合**でよい。運用回避・復旧手段があれば、システムによる厳密性は求めない。

## 全体構成

```
[状態専用リポジトリ (Gitea/GitLab)]  ← 唯一の共有点。git が輸送、ファイルが真実
        ▲ push/pull（force push 禁止・保護ブランチ）
        │
 ┌──────┴──────────┬─────────────────────┐
 │ PC-A            │ PC-B                │ PC-C（viewer 専用でも可）
 │ WSL: daemon     │ WSL: daemon         │ WSL: clone のみ
 │  node: pc-a     │  node: pc-b         │
 │ Win: dashboard  │ Win: dashboard      │ Win: dashboard
 └─────────────────┴─────────────────────┘
```

- **真実は常にファイル**（`backlog/` `needs/` `commands/` `decisions/` …）。git は輸送手段。
- dashboard は状態を**ファイル読取（5 秒ポーリング）**で表示し、操作は
  **ファイルドロップ（`commands/*.json`・`inbox/*.json`・`needs/<id>.md` 追記）だけ**を行う。
  backlog の status を直接書き換える経路は存在しない（`done` は verify のみが根拠）。
  → dashboard がいつ起動・終了しても、他 PC といつ競合しても、状態遷移の整合は壊れない。
- 各 PC の daemon は git-CAS（fast-forward push の成否）でタスクを 1 件ずつ claim する。
  分担は「早い者勝ち＋自動割当（ベストエフォート）」で、均等性は保証しない＝要件どおり。

## 設計原則（この 5 つだけ守る）

1. **状態リポジトリの名前空間ごとに writer は 1 種類**。
   `agent-project`（サブディレクトリ or 専用リポジトリ）へ書くのは各 PC の agent-project 本体のみ。
   dashboard・人手の編集は「リモート優先で取り込まれる入力パス」
   （`commands/` `inbox/` `needs/` `policy.md` `charter.md` `rules.md`）に限定する。
2. **PC 固有情報は共有 YAML に書かない**。`node` / `root` / `availability` は各 PC の
   local profile（`~/.agents/agent-project/profiles/<project>.yaml`、
   `agent-project.profile.yaml.example` 参照）に置く。共有 `agent-project.yaml` は全 PC で同一。
3. **状態は WSL 側 ext4 に置く**。`/mnt/c`（DrvFS）は flock が不安定で claim ロック・
   state 同期の前提が崩れる。dashboard からは `\\wsl.localhost\<distro>\...` 越しに読める。
4. **共有ブランチへの force push は全員禁止**（サーバ側の保護ブランチで強制推奨）。
   エンジンは絶対に force push しない設計（push 競合は fetch → 3-way merge → 再 push、
   指数バックオフ 5 回。失敗しても次の同期間隔で再試行）なので、これを人が壊さないこと。
5. **状態は専用リポジトリ方式（案1）を使う**（`docs/guides/state-repo-migration.md`）。
   worktree（`<repo>-agent-state`）方式は Python/JS の二重実装によるパス解決が残る
   壊れやすい経路のため、複数 PC 運用では専用リポジトリに寄せる。

## 設定

### 共有設定 `agent-project.yaml`（状態リポジトリ直下・全 PC 同一）

`agent-project.state-git.yaml.example` をベースに、複数 PC 直接分担なので
`coordination` を有効化する:

```yaml
root: .
workdir: work
watch: true

# 複数 PC が同じ backlog を直接分担する場合の必須設定
coordination: git-cas          # controller lease と task claim を remote HEAD の CAS で確定
controller_heartbeat_sec: 30
controller_lease_sec: 120
coordination_retries: 3

state_git_interval: 300        # 同期の最短間隔（秒）。反映を速めたければ短く
# default_node: pc-a           # node 未指定タスクの既定実行ノード。空だと「どの PC も拾える」
                               #   （git-cas があるので二重実行はしないが、拾わせたくない PC が
                               #    あるなら明示する）
```

ポイント:

- `coordination: git-cas` が **二重実行防止の本体**。`claims/` のローカルロックは
  PC を跨いだ排他にならない（同期対象外）ため、複数 daemon 構成では必須。
- controller lease により planner（charter 計画・triage・inbox 取り込み・自動割当）は
  常に 1 台だけ。lease はハートビートで維持され、切れれば他 PC が自動で引き継ぐ。
  **controller が落ちても実行中タスクは止まらない**（planner 機能だけが移る）。

### PC 固有 profile（各 PC の `~/.agents/agent-project/profiles/<project>.yaml`）

```yaml
schema_version: 1
project: myproj
node: pc-a                    # PC ごとに一意。タスクの `- node:` 割当・status/<node>.json に使う
root: /home/me/projects/myproj-state
project_config: /home/me/projects/myproj-state/agent-project.yaml
availability:                 # この PC の稼働スケジュール（他 PC に影響しない）
  timezone: Asia/Tokyo
  daily_stop: "23:00"
  drain_before_sec: 1800      # 停止 30 分前から新規 claim をやめる
  shutdown_grace_sec: 300     # 猶予後、自ノードの doing を CAS で ready に戻して自 SIGTERM
  clock_skew_tolerance_sec: 30
```

`daily_stop` による停止は **drain → doing の返却 → 停止**の順で行われるため、
夜間に PC が落ちてもタスクは他 PC が翌朝拾える（返却時に claim token が新しくなるので、
遅れて届いた旧結果はフェンシングで棄却される）。

### PC 起動時の自動起動（WSL）

profile が絶対パス必須なのは自動起動の cwd 非依存のため。例（Windows タスクスケジューラ、
ログオン時）:

```
wsl.exe -d <distro> -u <user> -- agent-project start --profile myproj
```

`start` は常駐（`run --watch`）を detached 起動し、多重起動はインスタンスレジストリ
（`~/.agent-project/instances/`）で抑止される。クラッシュ残骸（孤児 agent-flow・
stale lock・中断 rebase）は次回起動時に自動回収される。

## 分担のしかた

### エージェント実行リソースの分担

- **割当なし（既定）**: ready なタスクはどの PC の daemon も拾える。git-CAS claim の
  早い者勝ち。controller が `allocate_distributed_tasks` で未割当タスクを生存ノードへ
  ベストエフォート配分する（ready+doing の少ないノード優先）。
- **明示割当**: タスクの `- node: pc-b` で特定 PC に固定（重い GPU ジョブ等）。
  dashboard の revise、または人が backlog を編集して指定できる。

### 人の判断の分担

判断待ちは `needs/<id>.md`（proposed / review / blocked）に集まり、全 PC の dashboard に
同じワークリストが見える。分担は:

- **監視オーナー**: dashboard の「担当」設定（`assignments.json`）で「誰が見るか」を明示。
  これは表示用のサイドカーであり、エンジンの動作には影響しない（＝壊れても実害なし）。
- **回答**: 誰かの dashboard が feedback を書く → `needs/` はリモート優先パスなので
  同期で確実に engine へ届く。二人が同時に同じ needs に回答した場合は後勝ちになるが、
  結果は `decisions/` に DR として恒久記録されるため、運用（DR を見て再 revise）で回復できる。
- **コメント併記**: `reviews/<task-id>/*.json` は 1 コメント = 1 ファイルなので
  複数 PC の同時書き込みが自然にマージされる（衝突しない）。

## エラー耐性 — 何が起きたらどうなる/どうするか

| 事象 | システムの挙動（実装済み） | 運用側の対応 |
|---|---|---|
| push 競合 | fetch → 3-way merge → 再 push を指数バックオフで 5 回。パス別に決着（入力系はリモート優先・機械状態はローカル優先、journal は union merge）。**止まらない** | 不要 |
| push が恒久的に詰まる（wedge） | リトライ後 RuntimeError に ahead/behind と原因ファイルを表示。次パスで再試行 | 表示された foreign dirty file を退避。最悪 clone し直して `start`（状態リポジトリ＝バックアップそのもの） |
| PC がタスク実行中にクラッシュ | 他 PC からは `status/<node>.json` の鮮度で死活判定。stale な doing は **自動では横取りせず** blocked + needs 化（分散モード） | needs で「ready に戻す」を承認 → 他 PC が拾う。復帰した PC の旧結果は claim token 不一致で棄却される |
| controller の PC が落ちる | lease 失効（既定 120 秒）で他 PC が controller を自動引き継ぎ | 不要 |
| 同じタスクを 2 台が同時に取りに行く | CAS push は片方しか成功しない。負けた側は remote truth に巻き戻す | 不要 |
| dashboard を閉じている | 影響なし。dashboard は純粋な viewer + ファイルドロップ。起動時に現状を再読取 | 不要 |
| commands ドロップが不正/適用失敗 | `commands/<name>.json.err` に理由、成功は `commands/processed/` にレシート。dashboard がカード上に「送信→受理/失敗」を表示 | 失敗理由を見て再操作 |
| daemon 停止中に dashboard から操作 | ドロップはファイルとして残り、daemon 再開時に取り込まれる（結果整合） | 急ぐなら該当 PC で `agent-project start` |
| 時計ずれ | lease / availability に `clock_skew_tolerance_sec` を考慮 | NTP を有効に。許容を超えるずれだけ直す |
| 状態の破損・誤操作 | 全変更が状態リポジトリのコミット履歴に残る | `git log` で特定 → clone し直し or revert → `start` |

## やってはいけないこと（アンチパターン）

- **backlog/<id>.md の status を手や dashboard 改造で直接書き換える**。
  第二の writer はコミット競合と done 不変条件の破壊の源。操作は必ず `commands/` 経由。
- **共有ブランチへの force push / 履歴書き換え**。エンジンの CAS・フェンシングの前提が崩れる。
- **共有 `agent-project.yaml` に node や availability を書く**。全 PC が同じ node を名乗り、
  claim・割当・死活判定が全部壊れる。PC 固有値は profile へ。
- **状態を `/mnt/c` に置く**。flock・rename の原子性が保証されない。
- **1 つの状態名前空間（subdir/リポジトリ）に複数プロジェクトの daemon を向ける**。
  「1 名前空間 = 1 backlog = 各 PC 1 daemon」を守る。
- **worktree 方式のまま複数 PC 化**。先に専用リポジトリ方式へ移行する。

## 既知の弱点と、必要になったときだけ入れる小さな改善

現状の実装で複数 PC 運用は成立するが、調査で見つかった弱点が 3 つある。
いずれも**運用で回避可能**なので、実害が出るまでコードは触らない（影響範囲最小の原則）。

1. **死活判定の閾値不一致**: `instances/` ハートビート（TTL 90 秒）と `status.json`
   （`fresh_after_sec` 既定 600 秒）で鮮度窓が違い、長い LLM ステップ中に dashboard が
   「別マシンで稼働中」と誤表示することがある。→ 表示だけの問題。実害が出たら
   `fresh_after_sec` を actパスの実測に合わせて調整する。
2. **bus の自動選択が「新しさ」基準**: ローカル `bus/` と同期ミラー `agent-flow/` が
   両方あると、cancel/resubmit の宛先を取り違えうる。→ 複数 PC 構成では
   共有 YAML で `bus:` を明示し、曖昧さ自体をなくす。
3. **`default_node` 未設定 + node 未割当タスク**: git-cas があれば二重実行はしないが、
   「どの PC が拾うか」が完全に成り行きになる。→ 拾わせたくない PC がある運用になったら
   `default_node` を設定する。

## 復旧チートシート

```bash
# 状態リポジトリが詰まった/壊れた疑い → 作り直しが最速（状態repo＝バックアップ）
mv ~/projects/myproj-state ~/projects/myproj-state.bak
git clone git@gitea:team/myproj-state.git ~/projects/myproj-state
agent-project start --profile myproj

# ある PC の doing が固まった → needs に出る「ready へ戻す」を dashboard で承認するだけ
# （承認 = commands/ ドロップ → controller が CAS で status を戻す）

# daemon の生死確認（WSL 内）
agent-project status --root ~/projects/myproj-state
```
