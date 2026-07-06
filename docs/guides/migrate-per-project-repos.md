# 移行手順書 — 1リポジトリ複数プロジェクト → マルチリポジトリ＋専用 daemon

> **対象**: kiro-projects / kiro-flow / kiro-projects-viewer を、**1つの共有リポジトリに全プロジェクトを
> 同期する構成（変更前）**から、**プロジェクトごとに別リポジトリへ分け、kiro-projects が
> プロジェクト単位の kiro-flow daemon を起動・監視する構成（変更後）**へ移行する運用手順。
>
> **前提**: 本体（kiro-projects daemon）は 1 台のホスト（例: サーバ WSL）で `--project all` 常駐している。
> 状態は `state_git`（＋ kiro-flow の `state_git`）で共有リポジトリへ鏡写ししている。

---

## 0. この移行で何が変わるか

| 項目 | 変更前 | 変更後 |
|---|---|---|
| kiro-projects の状態 | 共有 1 リポジトリの `kiro-projects/projects/<name>/`（全プロジェクト同居） | **プロジェクトごとの別リポジトリ**（`default` は個人・他はチーム）。`state_git_projects` で写像 |
| kiro-flow の run | 共有バス 1 本 → 共有リポジトリの `kiro-flow/` | **per-project バス**（`<root>/projects/<name>/bus`）→ 各プロジェクトのリポジトリの `kiro-flow/` |
| daemon | 共有バスの daemon を手動起動（1 台） | **kiro-projects が per-project daemon を起動・監視**（`manage_flow_daemon`） |
| 委譲の待ち方 | 同期（ブロック）。`act_timeout` で誤タイムアウト→retry | **非ブロッキング（`act_async`）**＋タイムアウト無限化で retry ループ解消 |
| アクセス制御 | 1 リポジトリ＝全員が全プロジェクトを見る | **リポジトリのメンバー＝そのプロジェクトの担当者**（アサインの違いをリポジトリ設定で解決） |

**重要な前提（移行が安全な理由）**: 状態の**真実はローカルのコンテナ**（`<root>/projects/<name>/`）にある。
リポジトリはその鏡。したがって**リポジトリ間でデータを手で移す必要は基本ない**——リポジトリの割り当てを
変えて再同期すれば、各プロジェクトのローカル subtree が新しいリポジトリへ初回同期で push される。

---

## 1. 事前準備（計画）

1. **プロジェクトの棚卸し**
   ```bash
   kiro-projects list                 # 稼働中の監視インスタンス
   ls <root>/projects/                # 実体（例: default alpha beta …）
   ```
2. **プロジェクト → リポジトリの割り当てを決める**
   - `default` … あなた個人のリポジトリ（private）。
   - `alpha` / `beta` … それぞれチームのリポジトリ（メンバー＝担当者に限定）。
3. **新しいリポジトリを作る**（GitLab/GitHub 等）
   - 空リポジトリを作成（`default` 用・各プロジェクト用）。
   - **`main` を保護ブランチ**にし、**force push 禁止**。メンバーに push 権限を付与。
   - 本体（daemon 実行ユーザー）の SSH 鍵で各リポジトリへ `git push` が通ること。
4. **worker 予算を決める**（マシン全体で同時に走らせる worker 数。プロジェクト数で割られる）。

---

## 2. 本体（サーバ）— 停止と最終同期

移行中の取りこぼしを防ぐため、いったん静止させる。

```bash
# 1) 監視を止める（SIGTERM → 登録掃除）
kiro-projects stop

# 2) 最新状態が旧リポジトリへ push 済みか確認（journal に "state-git 同期: export=…" があること）
tail -n 30 <root>/projects/<name>/journal.md

# 3) kiro-flow daemon を止める（共有バスの daemon）。in-flight run の扱いは §6 FAQ 参照
#    （プロセスを終了。lock は $TMPDIR/kiro-flow-locks/ に残るが cleanup が回収する）
pkill -f "kiro-flow.*daemon" || true
```

> **in-flight の gitlab 委譲がある場合**: 急がなくてよい。§6 の通り、移行後に同じ決定的 run_id で
> 再 submit され、**gitlab イシューはトークンで再アタッチ**されるので二重起票にはならない。
> 途中結果（`results/`）は引き継がれず再ポーリングから再開になる点だけ許容する。

---

## 3. 本体の設定（`~/.kiro/kiro-projects.yaml`）

**変更前（例）**:
```yaml
root: /home/me/kiro/.kiro-projects
workdir: /home/me/kiro
bus: /home/me/kiro/.kiro-flow-bus        # ← 共有バス（これがあると per-project daemon は無効）
executor: gitlab
watch: true
act_timeout: 1800
state_git: git@gitlab.example.com:team/kiro-state.git   # ← 全プロジェクト同居の共有リポジトリ
state_git_subdir: kiro-projects
```

**変更後**:
```yaml
root: /home/me/kiro/.kiro-projects
workdir: /home/me/kiro
# bus: を削除（共有バスをやめる）。未設定なら per-project バス <root>/projects/<name>/bus になり、
#      per-project daemon の対象になる。★ここを消し忘れると daemon は起動されない。
executor: gitlab
watch: true

# --- 状態リポジトリ（default=個人・他=プロジェクト固有） ---
state_git: git@gitlab.example.com:me/kiro-personal.git   # 既定＝未記載プロジェクト（default 含む）の落とし先
state_git_subdir: kiro-projects
state_git_projects:
  alpha: git@gitlab.example.com:team-alpha/kiro-state.git
  beta:
    remote: git@gitlab.example.com:team-beta/kiro-state.git
    interval: 120

# --- 実行層 daemon を kiro-projects が起動・監視 ---
manage_flow_daemon: true
flow_config: ~/.kiro/kiro-flow.yaml       # executor/gitlab 等の共有設定を各 daemon に --config で渡す
flow_max_workers: 6                        # マシン全体の予算（対象プロジェクト数で割り各 daemon の上限に）

# --- 非ブロッキング委譲＋タイムアウト無限化（gitlab 長期委譲の retry ループを消す） ---
act_async: true
act_timeout: 0
```

**kiro-flow 側（`~/.kiro/kiro-flow.yaml`）** — 共有設定はそのまま。gitlab の待ちを無限化する:
```yaml
executor: kiro            # or 各 daemon には kiro-projects が --executor gitlab を注入する
gitlab:
  repo_url: https://gitlab.example.com/team/tasks
  timeout: 0              # 全体タイムアウト無効（0=無限）
  approved_timeout: 0     # 承認/決着待ちの猶予も無限
# state_git は書かない（per-project の宛先は kiro-projects が daemon 起動時に CLI で注入する）
```

> 参考サンプル: [`kiro-projects.state-git.yaml.example`](../../tools/kiro-projects/kiro-projects.state-git.yaml.example) /
> [`kiro-flow.state-git.yaml.example`](../../tools/kiro-flow/kiro-flow.state-git.yaml.example)。

---

## 4. 本体のデータ（ローカルが正なので手移しは基本不要）

1. **旧・コンテナ丸ごとクローンを撤去**（per-project 用に新しく作り直すため）:
   ```bash
   rm -rf <root>/.state-git            # 変更前のコンテナ一括同期クローン（もう使わない）
   ```
   per-project 同期は `<root>/projects/<name>/.state-git` を各プロジェクトで新規に作る。

2. **旧・共有バスを撤去**（per-project バスへ切替）:
   ```bash
   rm -rf /home/me/kiro/.kiro-flow-bus   # 旧共有バス。新しくは <root>/projects/<name>/bus を使う
   ```
   （在庫の run は履歴として旧リポジトリの `kiro-flow/` に残る。移す必要はない。）

3. **（任意）履歴を新リポジトリに引き継ぎたい場合**だけ、旧リポジトリから subtree を切り出して
   新リポジトリへ入れる。通常は不要（状態は小さく、初回同期で再構築される）:
   ```bash
   # 例: 旧リポジトリの kiro-projects/projects/alpha/ を alpha リポジトリへ（git filter-repo 使用）
   git clone git@gitlab.example.com:team/kiro-state.git old && cd old
   git filter-repo --path kiro-projects/projects/alpha/ --path kiro-flow/    # 必要な範囲に絞る
   git remote add alpha git@gitlab.example.com:team-alpha/kiro-state.git
   git push alpha HEAD:main
   ```

> データ移行の実体は「ローカル `<root>/projects/<name>/` を新リポジトリへ初回同期で push」なので、
> 上記 3 はほとんどのケースで省略してよい。

---

## 5. 本体の起動と検証

```bash
# 1) 常駐起動（--project all）。manage_flow_daemon により各プロジェクトの kiro-flow daemon が
#    不在なら起動される（バスロックで冪等）。
kiro-projects start

# 2) daemon カバレッジと稼働の点検（未起動・設定漏れは warn で出る）
kiro-projects doctor

# 3) 各プロジェクトが自分のリポジトリへ初回同期しているか
#    （新リポジトリを clone して中身を確認）
git clone git@gitlab.example.com:team-alpha/kiro-state.git chk-alpha
ls chk-alpha/kiro-projects/projects/alpha/    # backlog / needs / decisions … が入っていること
ls chk-alpha/kiro-flow/                        # run が出れば flow も鏡写しできている

# 4) journal で per-project 同期・daemon 起動を確認
grep -E "state-git|kiro-flow daemon 起動|offload" <root>/projects/alpha/journal.md | tail
```

チェックリスト:
- [ ] `default` の状態が個人リポジトリへ、各プロジェクトがそれぞれのリポジトリへ push されている
- [ ] `kiro-projects doctor` に「kiro-flow daemon 不在」warn が出ていない
- [ ] gitlab 委譲タスクが `offloaded` になり、ループがブロックしない（他タスクが並行して進む）

---

## 6. 閲覧側（各メンバー・kiro-projects-viewer）

メンバーは**自分がアクセスできるリポジトリの clone を足すだけ**でドライブできる。

1. 担当プロジェクトのリポジトリを clone（鮮度維持と指示の書き戻しは
   [git-file-sync](../../tools/git-file-sync/) の bidirectional ペアで常駐同期）。
2. viewer の ⚙ 設定:
   - **コンテナのパス（roots）**: 各 clone の `<clone>/kiro-projects` を**1 行ずつ**追加（個人＋チームを混在可）。
   - **プロジェクト単位バス（`flowBusByProject`）**: `プロジェクト名 = <clone>/kiro-flow` を 1 行ずつ。
   - 旧・共有リポジトリの単一コンテナ／バス登録は削除。
3. offloaded タスクはバックログ行の「▶ run」バッジからフロータブの委譲中 run へ辿れる。

---

## 7. ロールバック

ローカルのコンテナ（`<root>/projects/<name>/`）は無傷なので、**設定を戻せば元の構成へ再収束**する。

```bash
kiro-projects stop
# kiro-projects.yaml を変更前へ戻す（state_git_projects / manage_flow_daemon / act_async を削除、
#   bus: と state_git を元の共有構成へ）。per-project クローンとバスは残しても害はない（使われなくなるだけ）。
rm -rf <root>/projects/*/.state-git       # 任意: per-project クローンの掃除
kiro-projects start
```

---

## FAQ

**Q. 移行時に in-flight の gitlab 委譲は失われる？**
A. 失われない。submit の run_id は（backlog パス, task.id, retries）で**決定的**で、backlog パスは移行で
変わらない。よって新しい per-project daemon が同じ run_id で再 submit し、**gitlab イシューは決定的
トークンで再アタッチ**される（重複起票なし）。ただし旧バスの途中結果（`results/`）は引き継がれず、
イシューの再ポーリングから再開になる。急ぐなら移行前に in-flight を drain してもよい。

**Q. `manage_flow_daemon: true` にしたのに daemon が起動しない。**
A. `bus:`（共有バス）が設定されたままだと per-project バスにならず対象外になる。`bus:` を削除する。
また対象は「`state_git_projects` を使い、かつ per-project バス、かつ落とし先リポジトリが解決できる」
プロジェクトだけ。`kiro-projects doctor` の warn を確認。

**Q. worker が起動しすぎ／少なすぎ。**
A. `flow_max_workers` はマシン全体の予算で、対象プロジェクト数で割って各 daemon の `--max-workers` に
なる。プロジェクト数が増減したら値を見直す（既存 daemon の上限は再起動で反映）。

**Q. 履歴（コミットログ）は引き継がれる？**
A. 既定では引き継がれない（新リポジトリに初回同期でスナップショットが入る）。履歴が必要なら §4-3 の
`git filter-repo` で subtree を切り出して新リポジトリへ push してから起動する。

**Q. viewer で offloaded が「inbox」に見える。**
A. 旧バージョンの viewer。`offloaded` 対応版（本 PR）へ更新する。
