# S1 詳細設計: 状態専用リポジトリの唯一化と設定 2 層の責務分離

ステータス: ドラフト(詳細設計)
入力: [`2026-07-25-agent-improvement-spec.md`](2026-07-25-agent-improvement-spec.md) §3 S1(C1)
参照: [`2026-07-24-single-resident-controller-design.md`](2026-07-24-single-resident-controller-design.md) §4.1 / [`docs/guides/state-repo-migration.md`](../guides/state-repo-migration.md)
実装フェーズ: Phase 1(S3 と同時。host.yaml 拡張は 1 回で行う)

---

## 1. スコープ

やること:

1. 状態ルート = 状態専用リポジトリ(state repo)の通常 clone、を唯一の方式にする。worktree 方式と関連キー・関連コードを廃止する。
2. 設定を `agent-project.host.yaml`(ノード固有)と `agent-project.yaml`(状態リポジトリ直下・プロジェクト共有)の 2 ファイルに集約し、専有キー契約を機械検査(fail-fast)にする。
3. profile(`~/.agents/agent-project/profiles/`)と成果物リポジトリ側ブートストラップ yaml を廃止する。
4. 単発実行 `agent-project run` の cwd 契約を「状態 clone であること」に変更する。

やらないこと(スコープ外):

- repos.json からの `local` 撤去と共通リゾルバ(S3。ただし本設計の `state_top` 廃止は S3 リゾルバを前提に置くため、境界を §3.5 に明記する)
- 検収 UI・MR 一本化(S4)、検証(S5)
- dashboard 側の変更(プロジェクト一覧は engine/status.json 経由のままで、host.yaml の変更に追従不要。ドキュメント修正のみ)

---

## 2. 現実装の事実(変更対象の棚卸し)

### 2.1 設定の読み込みチェーン

- 解決順は CLI > profile(`PROFILE_LOCAL_KEYS` = root/node/project_config/availability のみ) > 設定ファイル > `CONFIG_DEFAULTS`(`configfile.py:266-287` `resolve_config`)。
- 設定ファイルの探索は `--config` 明示 → cwd → `cwd/.agents/` → `cwd/.agent/` → `~/.agents|~/.agent`(`configfile.py:205-223` `_find_config`)。
- `CONFIG_DEFAULTS` は約 110 キーが単一 dict に平置き(`configfile.py:47-189`)。
- host.yaml は常駐体専用の別系統(`resident_cli.py:65-107` `HostConfig`/`load_host_config`)で、`resolve_config` からは一切読まれない。子プロセスへは `--root`/`--config` の 2 引数だけを渡す(`resident_cli.py:256-266`)。

### 2.2 状態ルートの決定(build_config)

- `state_repo` 設定時: `_redirect_root_to_state_repo`(`state.py:182-199`)で clone へリダイレクト。**clone 失敗時は worktree 方式へ暗黙フォールバック**(`configfile.py:319-332`)。
- `state_repo` 未設定時: worktree 方式(`_redirect_root_to_state_worktree`、`state.py:98-120`)。
- 成果物リポジトリの top を `state_top` に、リダイレクト前の `--root` を `source_root` に保持(`configfile.py:314-315`, `:390-391`)。

### 2.3 worktree 方式の付帯機構(廃止対象)

- `_ensure_state_worktree` / `_migrate_state_into_worktree`(state.py)
- `commit_state`(`state.py:442-509`。state_commit/state_commit_interval/state_push を消費)
- `backup_state`(`state.py:333-423`。state_backup_branch へのミラー = ドリフト源)
- `adopt_mirror_edits` / `sync_mirror_edits`(`state.py:260-322`。本体側 `.agent-project` の鏡から設定編集を取り込む)
- 状態共有の正系統は `DirectStateGit`(`stategit.py:89-`)で、state repo clone に対しては export コミット・fetch/3-way 統合・push を単独で完結できる。つまり state repo 方式では commit_state/backup_state は冗長な二重書き手になっている。

### 2.4 `state_top` / `source_root` の消費者

| 消費者 | 用途 |
|---|---|
| `config.py:284-290` `_source_repo` | 検収 diff の成果物リポジトリ解決(`work_branch_changes` / `delivery_entries`) |
| `doctor.py:195-203` / `loop.py:288-294` | 未 push バックアップの警告(worktree 方式専用) |
| `instances.py:16` / `doctor.py:1044` | レジストリ・診断の表示用 root(リダイレクト前パス) |
| `stategit.py:883,900` | direct モード可否判定(worktree サブディレクトリの特例) |

### 2.5 ブートストラップの矛盾(仕様書 S1 の問題認識)

`state_repo:` は状態 clone を作る**前**に読める場所に必要 → 成果物リポジトリ直下の
`agent-project.yaml` がブートストラップを兼ねる(`docs/guides/state-repo-migration.md:115-168`)。
結果、設定の置き場が「成果物側 yaml・状態側 yaml・profile・host.yaml」の 4 か所に散る。

---

## 3. 設計

### 3.1 起動契約 — 状態ルートの決定を 1 本にする

状態ルートの決定アルゴリズムを次に置き換える(`build_config` 冒頭)。**worktree への分岐・フォールバックは存在しない**。

```
入力: --root(任意) / --state-repo(任意) / --project(新設・任意) / host.yaml(常時読む)

1. 対象決定
   a. --project <name>       → host.yaml projects[] の name 一致エントリ。無ければ fail-fast。
   b. --state-repo <url>     → ad-hoc 起動。root = --root(あれば)or <cwd>/<リポジトリ名>。
   c. --root <path> / cwd    → host.yaml projects[] の root(正規化絶対パス)一致エントリが
                                あればそれを採用(overrides が効く)。無ければ ad-hoc。
2. clone の確保(a/b で state_repo が既知のとき)
   - root が存在しない/空       → git clone --branch <branch> <state_repo> <root>
   - root/.git の origin 検査    → state_repo と不一致なら fail-fast(E3。暗黙フォールバック廃止)
3. ad-hoc 検査(state_repo 不明のとき。単発 run の cwd 契約)
   - root が git toplevel であること(worktree 内サブディレクトリ・管理外は fail-fast E4)
   - 「状態ルートらしさ」: 直下に agent-project.yaml / project.json / backlog/ / charter.md の
     いずれかがある、または空リポジトリ(コミット 0 or 状態エントリ 0 = 新規プロジェクト初期化)
   - 不合格(成果物リポジトリらしい)→ fail-fast E5。root/agent-project.yaml に state_repo: が
     残っていれば(旧ブートストラップ)、その URL から推定した clone 先を誘導文に含める
4. プロジェクト yaml = <root>/agent-project.{yaml,yml,json} 固定(--config 明示は残す。
   ./.agents → ./.agent → ~/.agents の探索チェーンは廃止)
```

決定事項:

- **`state_top`/`source_root` は Config から削除する。** 実効 root は常に状態 clone であり、リダイレクトという概念が消えるため。成果物リポジトリの解決は §3.5。
- **clone の確保は build_config が行い、常駐体は argv を渡すだけ**にする。serve の子 argv は `run --watch --root <projects[].root> --state-repo <url> --state-repo-branch <branch>` となり、単発起動と同じ検査・確保コードを通る(1 実装)。
- 新規プロジェクトの初期化は「空の state repo を作り `--state-repo` で起動」または「`git init` した空ディレクトリで起動」(リモート無しのローカル縮退は `DirectStateGit._has_remote()` の既存挙動どおりコミットのみ)。
- `--state-repo-dir` は廃止(root がその役割を担う)。`--state-repo-branch` は host.yaml `projects[].branch` の CLI 同義語として残す。

### 3.2 設定 2 層とキー分類契約

#### ファイルと責務

| ファイル | 置き場所 | 責務 | 共有範囲 |
|---|---|---|---|
| `agent-project.host.yaml` | `~/.agents/`(cwd フォールバックは現状維持) | ノードの宣言: 何を動かすか・資源・ローカル環境 | 共有しない |
| `agent-project.yaml` | 状態リポジトリ直下 | プロジェクトの合意: どう動かすか | state repo で全 PC 共有 |

#### host.yaml スキーマ(v1 に additive。`schema_version: 1` のまま)

```yaml
schema_version: 1
node_id: pc-a                        # 省略時 hostname 正規化(現行どおり)

defaults:                            # ノード全体の共有キー上書き(SHARED 群のみ可)
  agent_cli: codex
  model: gpt-5.6-sol

projects:
  - name: example                    # 省略時 root の slug(現行どおり)
    state_repo: https://git.example.com/example-state.git
    branch: main                     # 省略時 main(旧 state_repo_branch)
    root: /home/me/agents/example-state   # このノードでの clone 先(絶対パス)
    overrides:                       # このノード×このプロジェクトの上書き(SHARED 群のみ可)
      model: gpt-5.6-sol
    # config: は廃止(検出したら fail-fast E6)

repos:                               # S3: ノード固有ローカルクローン宣言(url/local の列)
  - url: https://git.example.com/app.git
    local: /home/me/mirrors/app

availability: {...}                  # 現行どおり(profile から移設される唯一の実質項目)
budget: {max_concurrent: 0}          # 現行どおり
tags: []                             # 板参加: ノード能力タグ(現行どおり)
agent_cli: []                        # 板参加: ノードで使える CLI の能力宣言(list。defaults.agent_cli
                                     #   =既定 CLI(scalar)とは別物。README/example に明記する)
board: ""                            # 板参加: 巡回する板(請負側)
board_workdir: null                  # git+ 板の clone 作業領域(ノードのローカルパス)
amigos_bus: ""                       # 現行どおり
amigos_config: null
residency: auto
update: {...}                        # 移設: 自動アップデート系(§ 分類表参照)
```

`HostConfig` に `defaults`(dict)・`projects[].state_repo/branch/root/overrides`・`repos`(list へ正規化)・`update` を追加する。既存フィールドは不変。

#### キー分類(CONFIG_DEFAULTS 全キーの帰属)

分類はコード上の 4 つの frozenset として `configfile.py` に置き、テーブルが唯一の契約になる
(`CONFIG_DEFAULTS` のキーは必ずいずれか 1 つに属することを CI テストで固定する)。

**HOST_ONLY — host.yaml 専有(プロジェクト yaml に書いたらエラー E1)**

| キー | 備考 |
|---|---|
| `node_id`(旧 `node`) | CLI `--node` / 環境変数 `AGENT_PROJECT_NODE` は同義として残す |
| `projects[]`(name/state_repo/branch/root/overrides) | state_repo は clone 前に必要なブートストラップ情報。ここが唯一の置き場 |
| `repos[]` | S3 のノード固有ローカルクローン宣言 |
| `availability` | profile から移設。プロジェクト yaml/CLI の availability 経路は廃止 |
| `budget.max_concurrent` | ノード資源上限 |
| `tags` / `agent_cli`(list) / `board`(参加) / `board_workdir` | 板への参加宣言・ノードのローカルパス |
| `amigos_bus` / `amigos_config` / `residency` | 現行どおり |
| `update_enabled` / `update_check_interval` / `update_repo` / `update_branch` / `update_subdir` / `update_installer` | **project yaml から移設**。ツールの自動更新はノードのインストール管理であり、プロジェクト共有設定に置くとノード間でツールバージョン操作が非対称に飛ぶ。host.yaml では `update:` セクションに畳む |

**PROJECT_ONLY — プロジェクト yaml 専有(host.yaml defaults/overrides に書いたらエラー E2)**

- 計画・ゲート系: `executor` `planner` `flow_planner` `route_planner` `granularity` `plan_review` `assess` `spec_track` `spec_threshold` `repo_map` `rules_capture` `agents` `hooks` `task_branch` `task_branch_prefix` `delivery_review`(S4 で `remote_review` が加わる)
- 予算・収束系: `max_cycles` `max_seconds` `max_tokens` `max_cost` `max_retries` `max_iterations` `max_spawn` `level` `auto_level` `auto_level_max` `level_promote_after` `level_window` `level_rework_max` `max_project_cycles` `max_project_cost` `project_stall` `auto_adjudicate` `adjudicate_max` `reject_recur`
- 検証系: `verify_confirm` `verify_validate`(S5 で廃止予定) `regression_cmd` `regression_revert`(S5 の検証設定もここ)
- 学習系: `learn` `learn_capture` `distill_learn` `intake_recall` `learn_threshold` `promote_threshold` `ltm` `rot_age_days`
- タスク運用系: `default_node` `default_workspace` `board`(公示先) `board_workload` `git_bus` `git_branch` `git_subdir` `intake_cmd` `intake_interval` `bus_keep_runs` `cleanup` `do_archive` `journal_max_bytes` `journal_keep` `throttle` `debounce` `pace` `poll` `require_progress`
- 同期・置き場系(root 相対で全ノード共通に解決できるもの): `workdir` `bus` `flow_config` `state_git_interval` `status_interval` `verify_cwd` と個別パスキー(`backlog` `policy` `decisions` `journal` `needs` `archive` `delivery` `inbox`)
- 協調系: `controller_heartbeat_sec` `controller_lease_sec` `coordination_retries` `clock_skew_tolerance_sec`

注: `board` は依頼側(このプロジェクトがどの板へ公示するか = 合意)と請負側(このノードがどの板を巡回するか = 参加宣言)で同名の別キー。前者はプロジェクト yaml、後者は host.yaml トップレベルに従来どおり置く。README とスキーマコメントに明記する。

**SHARED — 重複許可(両方に書ける。優先順位: CLI > overrides > defaults > プロジェクト yaml > 既定)**

`agent_cli`(scalar) / `model` / `act_timeout` / `verify_timeout` / `location` / `concurrency`
(仕様指定の 6 キー)+ 次の 3 キーを追加提案する(仕様の未決 1 への決着案):
`agent_timeout`(CLI 性能差) / `actor`(PC ごとの操作者名義) / `notify_cmd`(通知はノードのデスクトップ環境依存)。
`ltm_home` はノード局所パスだが ltm-use 側の既定解決があるため追加しない(必要になったら足す。SHARED への追加は非破壊)。

**CLI_ONLY — 設定ファイルに書けない実行時フラグ(現行どおり)**

`watch` `once` `dry_run` `force` `rot` `review_project` `with_flow` と `--config` `--root` `--state-repo` `--state-repo-branch` `--project`(新設)。

**REMOVED — 廃止(検出時の挙動は §3.3)**

`state_worktree_dir` `state_branch` `state_commit` `state_push` `state_backup_branch`(fail-fast E7)/
`state_commit_interval`(警告のみ・無視)/ `state_git`(警告のみ・無視。origin は clone が必ず持つため役目が無い。URL 設定の受け皿は host.yaml `projects[].state_repo`)/
`state_repo` `state_repo_dir` `state_repo_branch`(プロジェクト yaml に書いたら E1。CLI は §3.1 のとおり存続)

#### resolve_config v2(マージの実装)

```python
def resolve_config(args):
    host = load_host_config(args.host_config)          # 常時読む(無ければ空)
    entry = _match_project(host, args)                 # §3.1 の対象決定
    root  = _ensure_state_root(entry, args)            # §3.1 の clone 確保・ad-hoc 検査
    cfg_path = args.config or _project_yaml_at(root)   # <root>/agent-project.{yaml,yml,json} 固定
    project = _load_config_file(cfg_path) if cfg_path else {}
    _validate_layers(project, host, entry, cfg_path)   # E1/E2 + 未知キー警告(1 回)
    for key, dflt in CONFIG_DEFAULTS.items():
        if getattr(args, key, None) is not None:       # 1) CLI
            continue
        for layer in (entry.overrides, host.defaults): # 2) 3) SHARED 群のみ保持済み
            if key in layer: setattr(args, key, layer[key]); break
        else:
            setattr(args, key, project.get(key, dflt)) # 4) 5)
    args.node = args.node or os.environ.get("AGENT_PROJECT_NODE") or host.node_id
    args.availability = host.availability
    return args
```

検証(`_validate_layers`)は fail-fast:

- プロジェクト yaml に HOST_ONLY / REMOVED(state_repo 系)キー → E1(キー名列挙 + 「host.yaml へ移してください」)
- host.yaml の `defaults:` / `projects[].overrides:` に SHARED 以外のキー → E2(「プロジェクト yaml(状態リポジトリ直下)へ移してください」)
- どちらの層でも CONFIG_DEFAULTS・契約表に無いキー → 警告 1 回(typo 検出。既存の黙殺をやめる)

### 3.3 廃止と削除

#### fail-fast / 警告の文言カタログ

| ID | 契機 | 挙動 |
|---|---|---|
| E1 | プロジェクト yaml に host 専有キー | エラー終了。キー列挙 + host.yaml の該当セクションを提示 |
| E2 | host.yaml defaults/overrides に非 SHARED キー | エラー終了。同上(逆方向) |
| E3 | root の origin が projects[].state_repo と不一致 | エラー終了。「root を退避するか state_repo/root 宣言を直す」。**worktree への暗黙フォールバックはしない** |
| E4 | root が git toplevel でない | エラー終了。「状態リポジトリの clone を root にする / git init する」 |
| E5 | root が成果物リポジトリらしい(状態マーカー無し) | エラー終了。「状態 clone で起動する。旧ブートストラップ yaml は読まれません」+ state_repo: 残存時は clone 先の推定を提示 |
| E6 | host.yaml projects[].config | エラー終了。「設定は <root>/agent-project.yaml(状態リポジトリ直下)へ」 |
| E7 | worktree 5 キー(state_worktree_dir/state_branch/state_commit/state_push/state_backup_branch)をプロジェクト yaml・CLI で検出 | エラー終了。「migrate-state-repo.sh で状態専用リポジトリへ移行」+ ガイドのパス |
| W1 | `--profile` / profile ファイル参照 | エラー終了(受け口は argparse に残し前方一致事故を防ぐ・`_deprecated_coordination` と同じ流儀)。「host.yaml の projects[]/availability へ転記」 |
| W2 | 成果物リポジトリ側 agent-project.yaml の存在(serve 経路で projects[].root と別の場所に state_repo: 入り yaml を検知した場合を含め、積極探索はしない) | 起動時警告のみ(読まない)。E5 の誘導文で言及する |
| W3 | `state_commit_interval` / `state_git` | 警告のみ・無視 |
| W4 | 旧 `~/.agents/agent-project.yaml` 等、探索チェーンでしか見つからなかった設定 | 検出時に警告 1 回(「探索チェーンは廃止・<root>/ 直下のみ」)。実装は _find_config 削除時に「旧探索先にファイルが在るのに新契約で見つからない」場合のみ通知 |

#### 削除するコード

- `state.py`: `_redirect_root_to_state_worktree` `_ensure_state_worktree` `_migrate_state_into_worktree` `adopt_mirror_edits` `sync_mirror_edits` `backup_state` `commit_state` `_is_pushed` `_BACKUP_MSG` `_HUMAN_OWNED_STATE_FILES` `_commit_state_lock_path`(DirectStateGit 側ロックは維持)
- `configfile.py`: worktree 分岐(`:329-337`)、profile 系(`PROFILE_DIR` `PROFILE_LOCAL_KEYS` `_find_profile` `_load_profile`)、`_find_config` の探索チェーン、廃止キーの CONFIG_DEFAULTS エントリと `--state-worktree-dir` 等の argparse 受け口(E7/W1 用の SUPPRESS 受け口だけ残す)
- `config.py` Config: `state_worktree_dir` `state_branch` `state_commit` `state_commit_interval` `state_push` `state_backup_branch` `state_top` `source_root` `profile_mode` フィールド
- `stategit.py`: `_direct_state_git_ok`/`_ensure_direct_state_git` の worktree 特例(`state_top` 参照)を除去。状態コミットの書き手は **DirectStateGit ただ 1 つ**になる(commit_state との二重書き手を解消。significant/noise の即時/バッチの使い分けは DirectStateGit の interval 同期に一本化し、`state_git_interval` が唯一のノブ)
- `doctor.py:195-203` / `loop.py:288-294`: 未 push バックアップ警告 → 状態 clone の ahead(`DirectStateGit.observe_sync`)警告に置き換え
- `instances.py:16` / `doctor.py:1044`: `source_root` → 実効 root(状態 clone)に一本化
- `migrate-state-repo.sh`: 完了メッセージを「host.yaml へ projects[] を転記」誘導に更新
- example: `agent-project.profile.yaml.example` 削除、`agent-project.yaml.example` / `agent-project.state-git.yaml.example` / `agent-project.host.yaml.example` を 2 層契約で書き直し

### 3.4 常駐体(serve)との整合

- `_project_child_spec` は `--state-repo`/`--state-repo-branch` を付けて子を起動する(§3.1)。clone 確保・検査は子の build_config が行うため、**常駐体に git 操作は増えない**。clone 失敗・E3 は子の起動失敗として Supervisor の既存の隔離(quarantine)に乗り、status.json の recent_errors で dashboard から見える。
- `defaults:`/`overrides:` は子が自分で host.yaml を再読(root 一致でエントリを特定)して適用する。argv で運ぶ必要はない(§3.2 resolve_config v2)。
- `worker init`(`resident_cli.py:605-631`)は生成物に `defaults: {}` `projects: []` `repos: []` を含める形へ更新。専有契約はワーカーノードでも同一(仕様の未決 1 は「worker init も同じ v1 additive スキーマを書き、検証コードも共通」で決着)。
- `cmd_start`/`cmd_stop` 等の旧単発系(`_resolved_root` の profile 引数)は profile 廃止に追従。

### 3.5 成果物リポジトリ解決(旧 state_top)の置き換え — S3 との境界

`_source_repo(cfg)`(検収 diff・`work_branch_changes` の対象リポジトリ)は「リダイレクト前の cwd」という暗黙アンカーを失う。置き換えは:

1. タスクの workspace spec の `url` を取り、**S3 の共通リゾルバ**(`agentcore`、URL 正規化一致で host.yaml `repos[].local` を引く)でローカルパスに解決する。
2. 解決できなければ `<root>/.cache/repos/<slug>` への管理 clone(fetch 再利用)に落とす。verify の「charter に単一 repo があれば一時 clone」(既存挙動)と同じ機構を共通化する。
3. S4 適用後はローカル diff の用途自体が縮小し、2 の頻度は下がる(本設計では 2 を恒久フォールバックとして持つ)。

シグネチャは `_source_repo(cfg, task)`(task の workspace が要るため)。S1+S3 を同一フェーズで実装する根拠がこれで、リゾルバ関数のインターフェース
`resolve_local_repo(host: HostConfig, url: str) -> Path | None` だけを本設計の前提として固定する。

### 3.6 doctor の拡張

- 新検査: E1〜E7 相当の設定検査を warn でなく起動前チェックとして再掲(起動できないプロジェクトの原因を serve 側からも診断できるように)
- 新検査: host.yaml projects[] の root 存在・origin 一致・branch 一致
- 削除: worktree/バックアップ系検査、profile 検査、residency 検査は現行維持

---

## 4. 移行

### 4.1 既存プロジェクト(state_repo 設定済み)

1. 成果物側 yaml の `state_repo`/`state_repo_branch` を host.yaml の `projects[]` へ転記し、`root:` を状態 clone の実パス(`<repo>-state`)へ変更する(旧 root = 成果物リポジトリは E5 で弾かれるため、転記漏れは起動時に必ず露見する)。
2. 運用設定(planner/level 等)は状態リポジトリ直下の `agent-project.yaml` へ移す(state repo は `_STATE_SIGNIFICANT` に `agent-project.yaml` を含むため、既に同期対象)。
3. 成果物側 yaml は削除(残っていても W2 警告のみ)。

### 4.2 worktree 方式のままのプロジェクト

`migrate-state-repo.sh`(既存)で状態を専用リポジトリへ移してから 4.1。E7 の誘導文がこの順序を案内する。

### 4.3 profile 利用ノード

`root`→`projects[].root`、`node`→`node_id`、`availability`→`availability`、`project_config`→廃止(状態リポジトリ直下へ)。W1 の誘導文に対応表を載せる。

### 4.4 ドキュメント

- `docs/guides/state-repo-migration.md`: 「デーモン起動時のカレントパス(cwd)」以降の章を 2 層契約で全面改訂(ブートストラップ yaml の記述を撤去)
- README / GUIDE の設定探索順・`.agent/` 旧表記・dashboard README の旧「ワークスペース登録」記述の修正(仕様書の移行節)
- CHANGELOG: 破壊的変更(E5/E7/W1)の明記

---

## 5. テスト計画

新規(test_config.py / test_state_git.py / test_resident.py):

1. 層の優先順位: CLI > overrides > defaults > プロジェクト yaml > 既定(SHARED キーで全段を跨ぐケース)
2. E1/E2: 専有違反がキー名付きで fail-fast
3. E3: origin 不一致で終了(フォールバックしないこと)
4. E4/E5: 非 git・成果物リポジトリ cwd で終了、状態マーカー有り/空リポジトリで起動可
5. E7/W1/W3: 廃止キー・profile の検出挙動
6. `--state-repo` ad-hoc: 新規 clone → 起動 → 2 回目は再利用
7. serve: projects[] から子 argv 生成(--state-repo 伝搬)、E3 相当が quarantine + recent_errors に乗る
8. CONFIG_DEFAULTS 全キーが分類 frozenset のいずれか 1 つに属する(契約の CI 固定)
9. `_source_repo(cfg, task)`: resolver ヒット/ミス(管理 clone フォールバック)

改修: worktree 前提のテスト(test_state_git.py の大半・test_config.py の profile 系)は削除または state repo 前提へ書き換え。

---

## 6. 実装ステップ(PR 分割)

1. **PR-1**: キー分類 frozenset + `_validate_layers` + E1/E2/E7/W3(検証のみ先行。既存動作は不変で警告が出るだけの段階)
2. **PR-2**: host.yaml 拡張(defaults/projects[].state_repo/branch/root/overrides/repos, update 移設)+ resolve_config v2 + profile 廃止(W1)
3. **PR-3**: build_config の root 決定一本化(E3〜E5)+ worktree 機構削除 + DirectStateGit 一本化 + `_source_repo` 置き換え(S3 リゾルバと接続)
4. **PR-4**: serve/worker init 追従 + doctor 拡張 + example/ドキュメント/CHANGELOG

各 PR 完結で全テスト green を維持する。PR-3 が破壊的変更の本体。

---

## 7. 未決事項の決着(仕様書 §5-1 への回答)と残課題

| 項目 | 決着 |
|---|---|
| worker init と専有項目の整合 | worker init も同一スキーマ(additive v1)を書き、検証コードを共通化(§3.4) |
| overrides に許すキーの最終リスト | 仕様の 6 キー + `agent_timeout`/`actor`/`notify_cmd`(§3.2 SHARED)。追加は非破壊なので最小から始める |

残課題(本設計では保留):

- `agents:`(処理毎の agent_cli/model 上書き)と SHARED 群の合成順(overrides.model と agents.plan.model の優先)。現行の「agents が処理単位で最終勝ち」を維持し、変更しない。
- 状態リポジトリを持たない完全ローカル運用の公式サポート範囲(git init 縮退は動くが、ドキュメントでは非推奨と明記するに留める)。
- `~/.agents` 配下の host.yaml を複数ノードで共有配布する運用(node_id 省略 + hostname 自動)における `projects[].root` のパス差異。当面「共有配布するなら root をノード間で同一パスに揃える」を運用規約とする。
