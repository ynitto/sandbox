# P0 詳細設計: canary 前に直す 4 件

ステータス: 実装済み（詳細設計 + 実装で確定した差分を §9 に反映）
入力: [`2026-07-26-open-items-and-concerns.md`](2026-07-26-open-items-and-concerns.md) §6.1 / §7.1
参照: [常駐一本化 設計](2026-07-24-single-resident-controller-design.md) §4.2・§6 /
[S1 詳細設計](2026-07-26-s1-config-two-layer-detailed-design.md) /
[S4/S5 詳細設計](2026-07-26-s4-s5-review-and-verification-detailed-design.md) §3.5 /
[S8/S9-4 詳細設計](2026-07-26-s8-s9-4-board-ui-and-doctor-chat-detailed-design.md) §5.2
実装フェーズ: R1（実機 canary）の**前**。4 件とも canary の観測結果を汚す不具合。

---

## 1. スコープ

やること（総覧 §7.1 の 4 行）:

| # | 直すもの | 何が壊れているか |
|---|---|---|
| P0-1 | `serve` の SIGTERM 窓 | 起動直後に SIGTERM が届くと即死し、子だけが監督者不在で生き残る |
| P0-2 | ノード宛て指示の置き場ずれ | 正典構成で dashboard の投函先と常駐体の取り込み先が別ファイルシステム |
| P0-3 | `Config.node` の正規化漏れ | 大文字ホスト名の PC が板と状態リポジトリで 2 名義になる |
| P0-4 | `remote_review` の未配線 + 再発防止 | `observe` が到達不能。同型の欠落を構造テストで塞ぐ |

やらないこと（スコープ外）:

- P1〜P3（総覧 §7.2〜§7.4）。canary と並行して進めてよいものは分離する。
- 契約（スキーマ）の破壊的変更。本設計で契約に触るのは `engine/status.json` の
  `board` ブロックへの**追加**（`workdir`）1 件だけで、`CONTRACT_VERSION` は据え置く。
- `node_id` 切替そのものの自動化。P0-3 は名義の**導出**を 1 本にするだけで、
  既存ノードの改名は従来どおり[静止点の手順](../guides/node-id-cutover.md)に載せる。

**本設計で新たに見つけたもの**は §7 にまとめ、どの P0 項目へ織り込んだかを対応付ける。

---

## 2. 現実装の事実（実測・2026-07-26）

### 2.1 P0-1: `cmd_serve` の起動順序

`resident_cli.py:765-799`。実行順は次のとおり:

| 順 | 行 | 内容 | 所要 |
|---|---|---|---|
| 1 | 769 | `load_host_config()` | 即時 |
| 2 | 770 | 起動バナー print | 即時 |
| 3 | 775 | `_build_resident(host)` — **この中で `sup.start(spec.name)` = 子 Popen** | プロジェクト数分 |
| 4 | 776 | `write_status()` — `_observe_sync_health` が git 観測を含む | **数秒ありうる** |
| 5 | 777 | `sched.start()` | 即時 |
| 6 | 783-788 | `stopping = threading.Event()` + `signal.signal(SIGTERM/SIGINT)` | 即時 |
| 7 | 789-798 | `while not stopping.wait(1.0)` / `finally: graceful_shutdown(sup)` | — |

**3〜5 が窓**。ここで SIGTERM が届くと既定ハンドラで即死（rc `-15`）し、`finally` の
`graceful_shutdown` が走らない。子は `subprocess.Popen` の独立プロセスなので生き残り、
次回起動の Supervisor が同一プロジェクトに 2 本目の `run --watch` を起こす
（`resident_cli.py:779-782` のコメント自身が警告している事故）。

実測: `tests/test_resident.py::test_serve_exits_cleanly_on_sigterm` を 5 回連続実行して
**1 回失敗**（`SIGTERM で graceful 終了しなかった rc=-15`）。総覧 §6.1-2 の「3 回に 1 回」と
同じ現象を再現できている。テストは `status.json` の出現（= 4 の完了）を待ってから
SIGTERM を撃つので、実際に踏んでいるのは 5〜6 のあいだの窓（テストの窓は本番より狭い）。

`graceful_shutdown(sup)` は `sup` を必須引数に取るが、`cmd_serve` は
`release_claims` / `release_lease` / `announce_away` / `final_push` を**一切注入していない**
（§7-F2）。

### 2.2 P0-2: ノード宛て指示の置き場

| | 読み手（常駐体） | 書き手（dashboard） |
|---|---|---|
| 実装 | `resident_cli.py:283-291 node_commands_dir()` | `delegation/main/node-commands.js:34-39 resolveCommandsDir()` |
| 解決 | `$AGENT_COMMANDS_DIR` → `_agents_home()/commands` | `delegation.nodeCommandsDir` → `$AGENT_COMMANDS_DIR` → `agentHomeSubdir('commands')` |
| home | `AGENT_PROJECT_AGENTS_HOME` → `Path.home()/.agents`（**WSL 側**） | `os.homedir()/.agents`（**Windows 側**） |
| 旧ホーム | フォールバック無し | `~/.agents/commands` が無ければ `~/.agent/commands`（`agent-home.js:31-37`） |

同じ dashboard の `engine.js:40-79 agentsHome()` は
`engine.home` 明示 → Windows なら `wsl.exe … wslpath -w "$HOME/.agents"` → `os.homedir()/.agents`
の 3 段で WSL 側 home を解決して `engine/status.json` を読んでいる。**この経路だけが
通っていない。**

逃げ道の `delegation.nodeCommandsDir` は `features/delegation/config.js:9-18` の既定に
無く（`flowBusDirs` / `boardRepos` / `refreshSec` のみ）、設定画面にも入力欄が無い。
テスト（`test/delegation-board.test.js:131,159,179`）だけが渡している。

**さらに（新規発見・§7-A）**: 投函レコードの `board` フィールドに、dashboard は
`delegation.boardRepos[i]`（**板の作業ディレクトリ**）を入れる（`ipc.js:137,161,196`）。
常駐体は `resident_cli.py:362` で `target != host.board` を完全一致で判定する。
`host.board` は板の**所在**（ローカル dir か `git+<url>`）で、作業ディレクトリは
`host.board_workdir` 相当——正典構成（Windows の dashboard が UNC で読む作業クローン /
WSL の常駐体が `git+ssh://…` を宣言）では**必ず食い違い、全指示が `.err` へ落ちる**。
置き場を直しただけでは「押しても効かないボタン」は消えない。

### 2.3 P0-3: `node` 名義の導出

| 導出点 | 実装 | 正規化 |
|---|---|---|
| `HostConfig.node_id` | `resident_cli.py:116-117` | `normalize_node_id`（小文字・`[a-z0-9._-]`） |
| 板の `nodes/<id>.json` | `NodeCapability.write` | 上を使う |
| agent-flow / agent-amigos | `daemon.py:81` / `daemon.py:70` | `normalize_node_id` |
| **`Config.node`** | `configfile.py:725` → `_auto_node_name()`（`configfile.py:32-40`） | `re.sub(r"[^A-Za-z0-9_.-]+","-")`・**小文字化しない**・`[:60]` |
| **`status/<node>.json` のファイル名** | `loop.py:28` | `re.sub(...)`・**小文字化しない** |

`resolve_config`（`configfile.py:599-603`）は `args.node` を
**宣言（host.yaml `node_id`・正規化済み）> `AGENT_PROJECT_NODE` > 空** の順で埋め、
空なら `build_config` が `_auto_node_name()` を使う。つまり非正規形が `Config.node` へ
入る経路は 3 本ある:

1. `node_id` 未宣言・環境変数無し → `_auto_node_name()`（ホスト名の大文字が残る）
2. `AGENT_PROJECT_NODE=MyPC`（**正規化されない**・§7-D）
3. `--node MyPC`（同上）

`Config.node` は次の場所へ書き出される: `status.json` / `status/<node>.json` の `node`
（`loop.py:62`）・タスクの `- node:`（`coordination.py:361` 自動割当）・`claim_owner`・
`coordination/controller.json` の `node`。板側は小文字なので、大文字ホスト名の PC は
`status/DESKTOP-X.json` と `nodes/desktop-x.json` の 2 名義になる。人が板の端末一覧
（小文字）を見て書いた `- node: desktop-x` は `prioritize.py:28` の完全一致で
**どのノードも拾わないまま ready で固まる**。

`doctor --node-id-cutover`（`doctor.py:262-345`）が見るのは板の `delegations/<id>/status/`・
amigos の `missions/<id>/status/`・板の `nodes/<new>.json` の 3 つだけで、
プロジェクト状態リポジトリ側（`status/` / backlog の `claim_owner` / `- node:`）は見ない。

### 2.4 P0-4: `remote_review` の配線

- `CONFIG_DEFAULTS["remote_review"] = "settle"`（`configfile.py:182`）。
  `SHARED_KEYS` にも `HOST_SOURCED_KEYS` にも無いので差集合で `PROJECT_ONLY_KEYS` に落ち、
  層検査は正しく通る（プロジェクト yaml 専有）。
- `Config` dataclass（`config.py:26-`）に**フィールドが無い**。`build_config` の
  `Config(...)` 呼び出しにも `remote_review=` が**無い**。
- 読み出しは `mr.py:397` の `getattr(cfg, "remote_review", "settle")` → **常に `settle`**。
  `mr.py:407-411` の observe 分岐は到達不能。
- テスト（`tests/test_delivery.py:412`）は `cfg.remote_review = remote_review` と
  **属性を手で生やして**呼ぶので緑のまま。`Config` は `slots` 無しの dataclass なので
  代入が通ってしまい、テストが配線の欠落を検出できない構造になっている。

機械照合の結果（`CONFIG_DEFAULTS` 111 キー対 `Config` フィールド）:

| キー | Config フィールド | `Config(...)` へ渡している | 実際の消費 |
|---|---|---|---|
| `remote_review` | **無し** | **無し** | `getattr` 既定に落ちて死んでいる |
| `root` | 無し | 無し | 構造上の入力（`under()` で全パスへ展開） |
| `journal_max_bytes` | 無し | 無し | モジュール大域 `batch._JOURNAL_MAX_BYTES`（`configfile.py:693-701`） |
| `journal_keep` | 無し | 無し | 同上 `_JOURNAL_KEEP` |
| `spec_threshold` ほか 2 | 有り | `**_spec_thresholds(args)` 経由 | 正しく届く |

つまり総覧 §6.1-1 の「Config へ届いていないのはこのキーだけ」は**不正確**（§7-B）。
除外リストは 3 件必要で、かつ「`Config(...)` の実引数名を静的に数える」検査は
`**_spec_thresholds(args)` を取りこぼす——検査は動的に組む必要がある。

---

## 3. 設計

### 3.1 P0-1 — シグナルハンドラを最初に置く

#### 3.1.1 新しい起動順序

```
def cmd_serve(args) -> int:
    stopping = _install_stop_signals()          # ★ 1. 何よりも先
    host = load_host_config(...)                #   2.
    print("[agent-project] serve: node_id=…")   #   3. バナー = 「もう安全」の合図
    sup = sched = None
    try:
        sup, sched, status, write_status, pool = _build_resident(host)   # 4. 子の起動
        if stopping.is_set():
            return 0                            #   → finally が子を畳む
        write_status()                          #   5. git 観測はここ（ハンドラ設置後）
        sched.start()
        print("[agent-project] serve: 起動しました（…）")
        while not stopping.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        print("[agent-project] serve: 停止します（子の graceful 停止）", file=sys.stderr)
        if sched is not None:
            sched.stop()
        if sup is not None:
            graceful_shutdown(sup)
        if sched is not None:
            sched.join(timeout=5.0)
    return 0
```

決めの理由:

- **ハンドラをバナーより前**に置く。バナーは「この行が出たらもうシグナルを取りこぼさない」
  という観測可能な境界になり、テストの待ち合わせ点として使える（§5.1）。
  `load_host_config` より前でも安全——この時点で畳むべき資源は無い。
- `sup` / `sched` を `None` で初期化し `finally` で個別に判定する。`_build_resident` が
  例外で落ちたときに `finally` が `NameError` を投げると、本当の原因が隠れる。
- `_build_resident` の**途中**で停止要求が入っても中断はしない。`Supervisor.start` は
  `Popen` を返すだけで長くはなく、`stop_all` は `proc is None` の子を no-op で飛ばす
  （`supervisor.py:86-88`）ので、部分的に起動した状態から畳んでも壊れない。
- 停止要求が入っていたら `write_status()` を**呼ばない**。`_observe_sync_health` の
  git 観測が数秒かかりうるので、停止の直前に「子は生きている」と書いた status を残しつつ
  systemd の停止猶予を食い潰すのは損しかない。

#### 3.1.2 `_install_stop_signals()`

```
def _install_stop_signals() -> threading.Event:
    """SIGTERM/SIGINT を graceful 停止要求へ変換する。2 度目は既定ハンドラへ戻す——
    停止処理が詰まったときに、人（Ctrl-C 2 回）と systemd（SIGKILL 前の再送）が
    諦める手段を残すため。"""
    stopping = threading.Event()

    def _handler(signum, _frame):
        if stopping.is_set():
            signal.signal(signum, signal.SIG_DFL)   # 2 度目は握らない
            os.kill(os.getpid(), signum)
            return
        stopping.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass    # メインスレッド以外（テスト等）では設定できない
    return stopping
```

関数に切り出すのは、後述の注入テストが「ハンドラが設置済みか」を
`_build_resident` の中から観測できるようにするため（`cmd_serve` の中にベタ書きだと
順序を外から確かめられない）。

#### 3.1.3 子（`run --watch`）側の同型の窓も同時に直す（§7-F1）

`doctor.py:1069-1080` は `state_sync(cfg)`（git fetch/push を含む）と
`start_controller_heartbeat(cfg)`（`controller.json` の lease を**取得する**）を実行してから
`_install_sigterm(cfg)` を呼ぶ。この窓で SIGTERM が届くと、lease を握ったまま `finally` を
通らずに死ぬ。次の子は `controller_lease_sec`（既定 120 秒）+ `clock_skew_tolerance_sec` の
あいだ controller に昇格できない。`systemctl restart` は親の SIGTERM →
`Supervisor.stop`（子へ SIGTERM）と伝わるので、**P0-1 が塞ぐのと同じ再起動経路で踏む**。

修正: `cfg.watch` のときの `_install_sigterm(cfg)` を `try:` ブロックの先頭
（`state_sync` より前）へ移す。`_install_sigterm` は SIGTERM を `KeyboardInterrupt` 化する
実装なので、`state_sync` 中に届けば既存の `except (KeyboardInterrupt, _StopRequested)` が
そのまま拾い、`finally` で lease と監視ロックを解放する——分岐を増やさずに窓だけが消える。

`availability` 監視の `SIGUSR1`（drain）も同じ呼び出しで設置されるので、あわせて前に出る。
こちらは早く効いても害が無い（drain は冪等）。

### 3.2 P0-2 — 投函先・旧ホーム・板の同定を 3 点セットで直す

#### 3.2.1 置き場を `engine.js` と同じ解決へ揃える

`node-commands.js`:

```js
const { agentsHome } = require('../../agent-project/main/engine');

function resolveCommandsDir(cfg) {
  const d = (cfg && cfg.delegation) || {};
  const declared = String(d.nodeCommandsDir || process.env.AGENT_COMMANDS_DIR || '').trim();
  if (declared) return expandHome(declared);
  // 明示が無ければ実行エンジンと同じ場所。Windows では WSL の $HOME/.agents を
  // wslpath で引く（engine.js が 60 秒キャッシュ付きで持っている解決をそのまま使う）。
  return path.join(agentsHome(cfg), 'commands');
}
```

- `agentHomeSubdir('commands')` の参照を落とす。**旧 `~/.agent` フォールバックは持たない**
  ——常駐体（`_agents_home()`）が旧ホームを見ないので、書けても届かない場所が増えるだけ。
  旧ホームしか無い環境は `delegation.nodeCommandsDir` で明示する（設定画面から辿れる）。
- `expandHome` の `s.startsWith('~')` は `~foo` まで展開してしまう。他モジュールと同じ
  `/^~(?=$|\/|\\)/` へ揃える（§7-G）。
- `delegation` から `agent-project` の main を参照する形になるが、`ipc.js` が既に
  `../../amigos/main/homes` を引いており、feature 境界の既存の流儀の内側。

#### 3.2.2 板の同定 — 作業ディレクトリではなく所在を渡す

常駐体は `engine/status.json` の `board` ブロックに**作業ディレクトリを 1 項目足す**
（`_board_participate_tick` / `board_state`。既存フィールドの意味は変えないので additive、
`CONTRACT_VERSION` 据え置き）:

```python
board_state = {
    "configured": True,
    "location": host.board,          # 既存: 板の所在（ローカル dir / git+<url>）
    "workdir": BoardRepo(host.board, workdir=host.board_workdir).dir,   # 追加
    …
}
```

`BoardRepo.dir`（`board.py:60-70`）は `git+` 板なら clone 先、ローカル dir 板なら
その絶対パス——**dashboard が `delegation.boardRepos` に並べているのと同じ実体**である。

dashboard 側:

1. `engine.js normalizeBoardStatus` に `workdir` を通し、`proj().toViewerPath()` で
   画面から見えるパス（UNC）にも寄せた `viewerWorkdir` を足す。
2. `ipc.js` の 3 か所（`delegation:nodeCommand` / `delegation:award` /
   `delegation:cancel`）で `boardRepo` → **所在** への写像を 1 関数に集約する:

```js
// 投函レコードの board は「板の所在」（schema: ローカル dir / git+<url>）。
// dashboard が持っているのは作業ディレクトリなので、実行エンジンの宣言と突き合わせて
// 所在へ翻訳する。翻訳できない板は、この端末の実行エンジンが参加していない板。
function boardLocationFor(cfg, boardRepo) {
  const st = engine.readStatus(cfg).board;
  if (!st || !st.configured) throw new Error('この端末は仕事のやり取り先に参加していません');
  if (!boardRepo) return st.location;                 // 単一板の既定経路
  if (!st.workdir) return '';                         // 旧エンジン: 省略して host.yaml の board に委ねる
  if (!pathsEqual(boardRepo, st.viewerWorkdir || st.workdir)) {
    throw new Error('この端末の実行エンジンは、その仕事のやり取り先に参加していません');
  }
  return st.location;
}
```

- **翻訳できないものは投函しない**（例外にして画面へ理由を返す）。`.err` になる指示を
  投函して「送信済み → 失敗」を見せるより、押した瞬間に理由が出るほうが短い。
- `workdir` を載せていない旧エンジンに対しては `board` を**省略**する。スキーマは
  「省略時は host.yaml の board」と定めており（`agent-node-command.schema.json`）、
  かつ常駐体は `board.read_post(did) is None` で公示の実在も確かめるので、
  別の板の指示が黙って適用される余地は id の衝突に限られる。
- `pathsEqual` は `src/main/project.js` の既存実装（UNC / `wsl$` / スラッシュ混在を吸収。
  `test/path-wsl-equality.test.js` が固定している）を使う。新しい比較を書かない。

#### 3.2.3 設定を画面から辿れるようにする

- `features/delegation/config.js` の既定へ `nodeCommandsDir: ''` を追加し、
  「空欄なら実行エンジンと同じ場所」をコメントで明記する。
- 設定画面（`orchestration.js globalSettingsSyncHtml`）の「実行エンジンの場所」節へ
  入力欄を 1 つ足し、`renderer.js` の load（1072 行付近）/ save（1201 行付近）へ配線する。
- **文言は R10 の語彙規約に従う**。`test/no-git-writes.test.js:142-164` の R10 検査は
  `globalSettingsSyncHtml` 〜 `globalSettingsRoutineHtml` の HTML を丸ごと見て
  `/\bnode\b|\bsync\b|\bresident\b|常駐体|ノード/i` を禁じている。ラベルは
  「**この端末への指示の受け渡し先**」、補助文は「空欄なら実行エンジンと同じ場所を使います」
  とする（「ノード」「同期」を書かない）。

### 3.3 P0-3 — 名義の導出を 1 実装にする

#### 3.3.1 `agentcore.nodeid.default_node_id()` を新設

```python
def default_node_id() -> str:
    """このホストの既定 node_id。ホスト名 → COMPUTERNAME → HOSTNAME の順に拾って
    `normalize_node_id` へ通す。板・状態リポジトリ・engine のどこから見ても同じ綴りに
    なることが不変条件なので、導出はここ 1 か所に置く。"""
    raw = (socket.gethostname() or os.environ.get("COMPUTERNAME")
           or os.environ.get("HOSTNAME") or "")
    return normalize_node_id(raw)
```

採用者:

| 現在 | 変更後 |
|---|---|
| `configfile._auto_node_name()`（独自 `re.sub` + `[:60]`） | `default_node_id()` を返すだけの薄い皮（名前はテストが参照するので残す） |
| `resident_cli.HostConfig.__init__`（`normalize_node_id(socket.gethostname())`） | `default_node_id()` |
| `resident_cli.cmd_worker_init`（同上） | `default_node_id()` |
| `agent_flow.daemon:81` / `agent_amigos.daemon:70` | `default_node_id()`（環境変数フォールバックの有無を揃える） |

既存テストへの影響: `test_auto_node_name_sanitizes_hostname`（`'my pc.local!'` → `'my-pc.local'`）
と `..._falls_back_when_empty_after_sanitize`（`'!!!'` → `'node'`）は `normalize_node_id` でも
同じ結果になる（実測確認済み）ので**そのまま緑**。挙動が変わるのは大文字を含むホスト名と、
60 文字を超えるホスト名（`[:60]` の切り詰めが無くなる・§7-C）だけ。

#### 3.3.2 `Config.node` を構築時に正規化する

`build_config`（`configfile.py:725`）:

```python
node=_resolved_node(args),
```

```python
def _resolved_node(args) -> str:
    """CLI / 環境変数 / host.yaml / ホスト名のどこから来ても、板と同じ綴りへ倒す。
    非正規形を黙って正規化すると『指定した名前で動いていない』ことに気付けないので、
    変換したときだけ 1 行警告する（S1 の『設定したのに効かない』を作らない）。"""
    raw = str(getattr(args, "node", None) or "").strip()
    node = normalize_node_id(raw) if raw else default_node_id()
    if raw and node != raw:
        print(f"[agent-project] node 名を正規形へ揃えました: {raw} → {node}"
              f"（板とファイル名の綴りを 1 つに保つため）", file=sys.stderr)
    return node
```

これで 2.3 の 3 経路（未宣言 / `AGENT_PROJECT_NODE` / `--node`）が同じ綴りへ落ちる。
`HostConfig.node_id` は既に正規化済みなので二重適用になるが、`normalize_node_id` は
冪等（`agentcore/tests/test_nodeid.py:29` が固定）なので値は変わらない。

#### 3.3.3 `status/<node>.json` のファイル名

`loop.py:28` の独自 `re.sub` を `normalize_node_id(node)` に置き換える。3.3.2 の後は
`cfg.node` が既に正規形なので結果は同値だが、**サニタイズ規則を 2 つ持たない**ことが目的
（板のファイル名側と規則が割れたのが今回の欠陥の根）。

#### 3.3.4 名義変更の受け皿（doctor とガイド）

大文字ホスト名の PC ではこの修正自体が名義変更になる。`node_id` 切替と同じ静止点扱いにし、
`doctor --node-id-cutover <旧名義>` の検査対象を広げる（`doctor.py doctor_node_id_cutover_findings`
へ引数 `state_roots` を追加し、`node_id_cutover_findings` が host.yaml の
`projects[].root` を渡す）:

| 追加検査 | 根拠 | 所見時の案内 |
|---|---|---|
| `<root>/status/<旧名義>.json` の残存 | 同期対象なので全 PC へ配られ、鮮度が切れるまで `_peer_nodes`（`coordination.py:41-54`）が**自分を他ノードと誤認**して CAS 経路へ入る。切れた後も dashboard の端末一覧に死んだ行として残り続ける | 常駐体停止後にファイルを削除してからコミットする |
| backlog に `claim_owner: <旧名義>` の doing タスク | 旧名義の claim は新名義から解放できない（`coordination.py:373-395` は `claim_owner != node` を飛ばす）。§7-E | 実行中タスクが終わるまで切替を待つ |
| backlog に `- node: <旧名義>` かつ `node_source` が `auto` でないタスク | 自動割当分は `allocate_distributed_tasks` が拾い直すが、人が指定した割当は**誰も拾わないまま ready で固まる** | `- node:` を新名義へ書き換える |

[`docs/guides/node-id-cutover.md`](../guides/node-id-cutover.md) の「手順 1（事前チェック）」へ
上記 3 所見の読み方を 1 段追記し、「大文字を含むホスト名の PC は本修正の適用が切替に当たる」
ことを前提節に明記する。

### 3.4 P0-4 — `remote_review` の配線と、同型の欠落を潰す構造テスト

#### 3.4.1 配線

- `config.py` の `delivery_review`（194 行）の隣へ:

```python
    # フォージ（MR/PR）側の決定的シグナルからの決着（S4-5）。settle=決着させる /
    # observe=journal に残すだけ（移行用）。値域の正規化は build_config で済ませる。
    remote_review: str = "settle"
```

- `build_config` の `Config(...)` へ:

```python
        remote_review=_one_of(getattr(args, "remote_review", "settle"),
                              ("settle", "observe"), "settle"),
```

  値域外は既定へ倒したうえで警告する（`agent_cli` 等と同じ流儀。黙って倒すと
  「observ」の綴り間違いに気付けない）。
- `mr.py:397-399` の `getattr` + 小文字化 + 値域クランプを削り、`cfg.remote_review` を
  そのまま読む。**`getattr` の既定値が残っていると、次に配線を落としても同じように
  静かに死ぬ**——読み手側で庇わないことが再発防止の一部。

#### 3.4.2 構造テスト（2 段）

新規 `tests/test_config_keys.py`。

**(1) 存在検査**: `CONFIG_DEFAULTS` の全キーが `Config` のフィールドであること。

```python
CONFIG_KEY_EXEMPT = {
    "root": "パスの起点。Config には root 自体でなく under() で展開した個別パスが載る",
    "journal_max_bytes": "append_journal（free 関数）が読む batch._JOURNAL_MAX_BYTES へ確定する",
    "journal_keep": "同上 batch._JOURNAL_KEEP",
}
```

テストは「除外は `CONFIG_KEY_EXEMPT` のキーだけ」かつ「各除外に空でない理由が付いている」
ことも検査する。**リストへ足すには理由を書くしかない形**にするのが狙い（総覧 §7.1 P0-4）。

**(2) 到達検査**: 各キーについて「既定だけで組んだ Config」と「そのキーだけ非既定値を
宣言して組んだ Config」を作り、当該属性が**変化すること**を確かめる。

- 宣言先はキーの層で決める: `HOST_SOURCED_KEYS` は host.yaml、それ以外はプロジェクト yaml
  （`PROJECT_ONLY_KEYS` / `SHARED_KEYS` はどちらもプロジェクト yaml に書ける）。
- 等値ではなく**差分**を見る。クランプ（`_spec_thresholds`）・小文字化（`agent_cli`）・
  パス解決（`under()`）で値が変換されても落ちない。
- 番兵は型から作る（bool は反転 / 数値は +7 / 文字列は既定 + `-sentinel` / list・dict は
  非空）。値域を持つキーは `_SENTINEL_OVERRIDES` に個別指定する（`location` → `"remote"` 等。
  初期実装で 10〜20 件を見込む）。**番兵も除外理由も無いキーはテストが落ちる**。

`Config(...)` の実引数名を静的に数える案は採らない——`spec_threshold` 系が
`**_spec_thresholds(args)` で渡っており（2.4）、静的検査だと誤検出になる。

---

## 4. 変更ファイル一覧

| # | ファイル | 変更 |
|---|---|---|
| P0-1 | `tools/agent-project/agent_project/resident_cli.py` | `_install_stop_signals()` 新設・`cmd_serve` の順序入れ替え |
| P0-1 | `tools/agent-project/agent_project/doctor.py` | `_install_sigterm(cfg)` を `state_sync` より前へ（§7-F1） |
| P0-2 | `tools/agent-dashboard/src/features/delegation/main/node-commands.js` | `resolveCommandsDir` を `engine.agentsHome` 経由へ・旧ホーム参照の除去・`expandHome` の正規表現 |
| P0-2 | `tools/agent-dashboard/src/features/delegation/main/ipc.js` | `boardLocationFor()` 新設・3 か所の `board:` を所在へ |
| P0-2 | `tools/agent-dashboard/src/features/delegation/config.js` | `nodeCommandsDir: ''` を既定へ |
| P0-2 | `tools/agent-dashboard/src/features/agent-project/main/engine.js` | `normalizeBoardStatus` に `workdir` / `viewerWorkdir` |
| P0-2 | `tools/agent-dashboard/src/renderer/sections/orchestration.js` / `renderer.js` | 設定欄の追加と配線 |
| P0-2 | `tools/agent-project/agent_project/resident_cli.py` | `board_state` へ `workdir` |
| P0-2 | `tools/agent-dashboard/src/features/delegation/README.md` | 置き場の記述を更新 |
| P0-3 | `tools/agent-tools/agentcore/agentcore/nodeid.py` | `default_node_id()` 新設 |
| P0-3 | `tools/agent-project/agent_project/configfile.py` | `_auto_node_name` を薄い皮へ・`_resolved_node()` 新設 |
| P0-3 | `tools/agent-project/agent_project/loop.py` | `node_status_path` のサニタイズを `normalize_node_id` へ |
| P0-3 | `tools/agent-project/agent_project/resident_cli.py` / `agent-flow` / `agent-amigos` の daemon | 既定採番を `default_node_id()` へ |
| P0-3 | `tools/agent-project/agent_project/doctor.py` | cutover 検査へ状態リポジトリ側 3 検査 |
| P0-3 | `docs/guides/node-id-cutover.md` | 前提と手順 1 へ追記 |
| P0-4 | `tools/agent-project/agent_project/config.py` | `remote_review` フィールド |
| P0-4 | `tools/agent-project/agent_project/configfile.py` | `Config(...)` へ配線・値域正規化 |
| P0-4 | `tools/agent-project/agent_project/mr.py` | `getattr` フォールバックの除去 |
| — | `docs/guides/single-resident-canary.md` | 「開始前」へ **P0 済み**の確認行 |

---

## 5. テスト計画

### 5.1 P0-1

| テスト | 形 | 何を固定するか |
|---|---|---|
| `test_serve_installs_stop_handler_before_starting_children`（新規・単体） | `signal.signal` を差し替えてハンドラを捕捉し、差し替えた `_build_resident` の中から**そのハンドラを呼ぶ**（= 子の起動中に SIGTERM が届いた状況を決定的に再現）。`cmd_serve` が 0 を返し、`Scheduler.start` が呼ばれず、`Supervisor.stop_all` が呼ばれることを検査 | 窓そのもの。時間に依存しない |
| `test_serve_exits_cleanly_on_sigterm_at_banner`（新規・subprocess） | 子プロセスの stdout を 1 行ずつ読み、起動バナー（`serve: node_id=`）が出た**直後**に SIGTERM。rc 0 を要求 | 「バナー以降は取りこぼさない」という順序の約束 |
| `test_serve_exits_cleanly_on_sigterm`（既存） | 変更しない | 従来の窓（status.json 以降）。修正で決定的に緑になる |
| `test_watch_installs_sigterm_before_state_sync`（新規・単体） | `state_sync` を差し替え、その中で SIGTERM ハンドラが設置済みかを検査 | §7-F1 の窓 |

**リトライで誤魔化さない**（総覧 §7.1 の要求）。既存テストは 5 回連続実行で 1 回落ちる
状態を実測済みなので、修正後に同じ 5 回ループが全緑になることを完了条件に含める。

### 5.2 P0-2

| テスト | 形 |
|---|---|
| `resolveCommandsDir` が `engine.home` 配下を返す | `{engine:{home:'\\\\wsl.localhost\\Ubuntu\\home\\me\\.agents'}}` → `…\.agents\commands`。`os.homedir()` を返さないこと |
| 明示 `delegation.nodeCommandsDir` が最優先 | 既存 `delegation-board.test.js` の呼び方が壊れないことの確認も兼ねる |
| 旧ホームへ落ちない | `~/.agent/commands` だけが実在する状態を作り、それでも新ホーム側を返すこと |
| 構造テスト（`no-git-writes.test.js` へ追加） | `features/delegation/main/*.js` が `os.homedir()` / `agentHomeSubdir` を直接使わない（= 実行エンジンのホーム解決を迂回しない） |
| 板の同定 | `board.workdir` に一致する `boardRepo` → レコードの `board` が `location`。不一致 → 例外。`workdir` 未宣言の旧エンジン → `board` 省略 |
| 往復 | 投函 → `processed/<name>.json` を置く → `nodeCommandStatus` が `done` になる（`command-receipt.test.js` の流儀）。パスは Windows 形の `engine.home` で通す |
| Python 側 | `node_commands_dir()` が `_agents_home()/commands` であること（両実装が「同じ規則」であることを両側に 1 本ずつ置く） |

### 5.3 P0-3

| テスト | 内容 |
|---|---|
| 名義の一致 | ホスト名 `DESKTOP-X` で `Config.node` == `HostConfig.node_id` == `NodeCapability.node` == `status/<file>.json` のファイル名 |
| 環境変数・CLI の正規化 | `AGENT_PROJECT_NODE=MyPC` / `--node MyPC` → `cfg.node == "mypc"` と警告出力 |
| 回帰 | `task_runnable_here` が小文字の `- node: desktop-x` を大文字ホスト名の PC で拾う |
| 冪等 | `default_node_id()` を 2 度通しても綴りが変わらない |
| doctor | `status/<旧>.json` / `claim_owner: <旧>` / 手動 `- node: <旧>` のそれぞれで所見が 1 件出る。残骸ゼロなら所見ゼロ |
| 既存 | `test_auto_node_name_*` 2 件を**変更せずに**緑（挙動が変わるのは大文字だけ、の裏取り） |

### 5.4 P0-4

| テスト | 内容 |
|---|---|
| 到達 | プロジェクト yaml に `remote_review: observe` を書いて `build_config` を通し、`cfg.remote_review == "observe"` |
| 決着抑止 | 上で得た **`Config` そのもの**で `poll_task_mrs` を回し、merged MR が done にならず journal に `remote_review(observe)` が残る。既存 `tests/test_delivery.py` の `cfg.remote_review = …` 直代入をやめ、設定ファイル経由に置き換える（直代入のままだと配線を落としても緑のまま） |
| 値域 | `remote_review: observ` → 警告して `settle` |
| 構造 (1) | `CONFIG_DEFAULTS` ⊆ `Config` フィールド ∪ `CONFIG_KEY_EXEMPT`、除外に理由必須 |
| 構造 (2) | 全キーの到達検査（番兵法）。番兵も除外も無いキーで落ちる |

### 5.5 まとめて回す

CI はまだ無い（総覧 §1.2 / P3-1）ので、P0 のあいだは手元で次を回す:

```
python3 -m unittest discover -s tools/agent-project/tests
python3 -m unittest discover -s tools/agent-flow/tests
python3 -m unittest discover -s tools/agent-amigos/tests
python3 -m unittest discover -s tools/agent-tools/agentcore/agentcore/tests
python3 -m unittest discover -s tools/agent-tools/agentcore/tests     # ← テストルートが 2 つ（§6.3）
cd tools/agent-dashboard && npm test
```

---

## 6. 実施順序と静止点

1. **P0-4 → P0-1 → P0-2 → P0-3** の順。
   - P0-4 と P0-1 は他へ影響しない局所修正（S）。先に入れて回帰の基準線を作る。
   - P0-2 は `engine/status.json` へ additive な追加を含むので、常駐体 → dashboard の順に
     配って良い（古い dashboard は `workdir` を読まないだけ）。
   - **P0-3 は最後・静止点で**。大文字ホスト名の PC にとっては名義変更なので、
     [node-id-cutover ガイド](../guides/node-id-cutover.md)の手順（実行中の委譲・
     ミッション・doing タスクが無いことを doctor で確認 → 常駐体停止 → 適用 → 残骸削除）に
     従う。フリート全体の更新規律（常駐一本化設計 C13）どおり、全ノードを同一コミットへ。
2. 完了条件（総覧 §7.1）: 上記テストが緑 + canary ランブックの「開始前」に **P0 済み**を記録。
3. その後 R1（実機 canary・総覧 §1.1）へ入る。

---

## 7. 本設計の過程で新たに見つけたもの

総覧 §6 に無いものだけを挙げる。A・F1 は P0 の中で直す前提で §3 に織り込んである。

| # | 内容 | 重要度 | 扱い |
|---|---|---|---|
| A | **ノード指示の `board` フィールドが常駐体の同定検査を必ず落ちる**。dashboard は板の**作業ディレクトリ**（`delegation.boardRepos[i]`）を入れ、常駐体は板の**所在**（`host.board`）と完全一致で比較する（`resident_cli.py:362`）。正典構成では UNC パス対 `git+ssh://…` で必ず不一致 → 全指示が `.err`。**§6.1-3 の置き場を直しても、これが残ると押しても効かないまま** | 高 | P0-2 §3.2.2 |
| F1 | **子（`run --watch`）にも同型の SIGTERM 窓**。`doctor.py:1077` の `_install_sigterm` が `state_sync`（git）と `start_controller_heartbeat`（lease 取得）より後。この窓で死ぬと controller lease を握ったまま `finally` を通らず、次の子が最大 `controller_lease_sec`（既定 120 秒）昇格できない。親の再起動が子へ SIGTERM を送る以上、P0-1 と**同じ経路**で踏む | 高 | P0-1 §3.1.3 |
| B | **`CONFIG_DEFAULTS` ⊄ `Config` は `remote_review` だけではない**。`journal_max_bytes` / `journal_keep` も Config フィールドを持たない（モジュール大域へ確定する別経路で正しく動いてはいる）。総覧 §6.1-1 の「Config へ届いていないのはこのキーだけ」は不正確で、除外リストは 3 件必要。あわせて `spec_threshold` 系が `**_spec_thresholds(args)` で渡るため、静的な実引数名検査では誤検出になる | 低（設計の前提の訂正） | P0-4 §2.4・§3.4.2 |
| C | **`_auto_node_name` の `[:60]` 切り詰め**が `normalize_node_id` に無い。60 文字を超える FQDN のホストは、P0-3 の統合で大文字を含まなくても名義が変わる。`normalize_node_id` 側へ上限を足すと flow / amigos の既存名義まで変わるので**足さない**——長い名義は P0-3 の切替対象に含める | 低 | P0-3 §3.3.1 に注記 |
| D | **`AGENT_PROJECT_NODE` と `--node` も正規化されない**。総覧 §6.1-4 は「未宣言・環境変数無し」の経路だけを挙げているが、環境変数と CLI から入る非正規形も同じ 2 名義を作る（host.yaml 宣言だけが `HostConfig` で正規化される非対称） | 中 | P0-3 §3.3.2 で 3 経路とも塞ぐ |
| E | **`claim_owner` の旧名義残骸**が cutover 検査に無い。旧名義で claim した doing タスクは新名義から解放できない（`coordination.py:373` は `claim_owner != node` を飛ばす）ので、lease ではなく**人が直すまで固まる**。手動割当（`node_source != "auto"`）の `- node:` も同じ | 中 | P0-3 §3.3.4 の doctor 検査に追加 |
| F2 | **`cmd_serve` の `graceful_shutdown` が設計 §4.2 の 4 ステップを 1 つも注入していない**（`graceful_shutdown(sup)` のみ・`resident_cli.py:797`）。子は自前の `finally` で claim と lease を解放するので実害は限定的だが、**子を持たないワーカーノードでは板への away 宣言も最終 push も行われない**。設計書は 5 段のシーケンスとして書いてある | 中 | P0 に含めない。canary の観測項目（板に「応答なし」で残る時間）として §5 のランブックへ記録し、R2b の設計で決める |
| G | `node-commands.js` の `expandHome` が `s.startsWith('~')` で、`~foo` のようなパスまで展開する（他モジュールは `/^~(?=$|\/|\\)/`） | 低 | P0-2 §3.2.1 のついで |
| H | `tests/test_delivery.py` が `cfg.remote_review = …` と**属性を手で生やして**いるため、配線が無くてもテストが緑になる。`Config` が `slots` 無し dataclass であることに依存した書き方で、同じ書き方が他のキーにもあれば同型の欠落を隠す | 中 | P0-4 §5.4 でこのテストを設定ファイル経由へ書き換える |
| I | **`state_repo` / `state_repo_branch` は設定ファイルのどの層からも `Config` へ届かない**（実装中に到達検査で検出）。`HOST_SOURCED_KEYS` に入っているのに `_host_layer` がこの 2 キーを流さないため、`Config` に載るのは `--state-repo` / `--state-repo-branch`（CLI 明示）だけ。clone 前に `_resolve_state_root` が `projects[]` から直接読むのが正の経路なので**実害は無い**が、`Config` のフィールドとしてはほぼ空のまま残っている | 低 | 到達検査の除外へ理由付きで登録（§9）。フィールド自体を消すかは P1 以降で判断 |

---

## 8. 積み残し（本設計では扱わない）

- **`Config` を `slots=True` にする**か（H の再発防止の上位版）。テスト以外にも動的属性を
  生やしている箇所（`cfg._controller_active`・`coordination.py:394`）があるため、
  棚卸しが要る。P1 以降。
- **`engine/status.json` のスキーマファイル化**。`resident/status.py` の docstring が
  「dashboard 連携が実装される P2 まではこの dataclass が契約の正」と書いたまま P2 は
  済んでおり、`board` ブロックのように dict のまま育っている項目がある。P3-1（CI）で
  ゴールデンテストを入れる際に決める。
- **F2（graceful 停止の 4 ステップ）**。R2b（ノード直轄実行）で「落札した仕事を持つ
  ワーカーノードが落ちる」経路が現実になるので、そこで一緒に設計する。
- P0-3 で `agent-flow` / `agent-amigos` の既定採番も `default_node_id()` へ寄せるが、
  **明示指定値（`--node-id` / `AGENT_AMIGOS_NODE` / `node.json`）を正規化するかは変えない**
  （cutover ガイドが「明示した値はそのまま使う」と約束している）。`Config.node` だけを
  正規化するのは、そこが板とプロジェクト状態の**両方**にファイル名として現れる唯一の値で、
  綴りが割れると回復に人手が要るため。この非対称は意図的なので、ガイドに理由を残す。

---

## 9. 実装で確定した差分

設計と実装がずれたところ。**本文は書き換えず、ここに理由付きで残す**（既存の詳細設計と同じ流儀）。

| # | 設計 | 実装 | 理由 |
|---|---|---|---|
| P0-1 | ハンドラ設置 → `load_host_config` → バナー | ハンドラ設置を**最初**（`load_host_config` より前）に置き、`SERVE_BANNER` 定数を切り出した | この時点で畳むべき資源が無いので前へ出せる。バナー文字列を定数にしたのは、テストが「ハンドラ設置後の最初の出力」という契約に依存するため（文言を変えるとテストが落ちる＝契約が明示される） |
| P0-2 | `BoardRepo` を `try` の**外**で構築して `board.dir` を状況へ載せる | `try` の**中**で構築し、`board_state["workdir"]` はそこで足す | 既存テスト `test_board_failure_is_recorded_not_raised` が `BoardRepo` の構築失敗（例外）でも tick を落とさないことを固定していた。外へ出すと Scheduler が常駐体を隔離してしまう。載らなかった場合の縮退（画面は `board` を省略して投函）は設計どおり |
| P0-3 | `Config.node` の正規化（3 経路）+ `status/` のファイル名 | それに加えて **`task_runnable_here` の照合も正規形で行う** | 名義を正規化しても、**正規化前に書かれたタスクファイル**（`- node: DESKTOP-X`）はそのまま残る。読む側でも同じ規則で突き合わせないと、切替直後に「誰も拾わない ready」が残ったままになる。doctor の残骸検査（§3.3.4）は掃除のためにそのまま要る |
| P0-3 | `normalize_node_id` を各所で import | 共有 import（`_head.py`）に `normalize_node_id` / `default_node_id` を置いた | 断片は共有名前空間へ exec 合成される。`doctor` が `configfile` より先だから今は動く、という読み込み順への暗黙依存を作らない |
| P0-4 | 除外は `root` / `journal_*` の 3 件 | それに加えて到達検査の除外に `state_repo` / `state_repo_branch` を足した（§7-I） | `_host_layer` がこの 2 キーを流しておらず、設定ファイルからは届かない。`_resolve_state_root` が clone 前に `projects[]` から直接読むのが正の経路 |
| P0-4 | 番兵は型から生成し、値域を持つキーだけ個別指定 | 個別指定は 10 件（`remote_review` / `verify_side_effects` / `plan_sections` / `location` / `level` / `spec_threshold` 系 3 / `hooks` / `agents`） | 実測。見込み（10〜20）の下限で収まった |

### 9.1 実測（実装後）

| 対象 | 結果 |
|---|---|
| `tests/test_resident.ResidentCliTests` を 10 回連続 | 10/10 緑（修正前は `test_serve_exits_cleanly_on_sigterm` が 5 回中 1 回失敗） |
| agent-project | 1,082 件 緑 |
| agent-flow / agent-amigos | 571 / 176 件 緑 |
| agentcore（テストルート 2 つ） | 74 / 53 件 緑 |
| agent-dashboard `npm test` | 緑（失敗 0） |

`npx eslint .` はこの環境では devDependencies が未インストールのため実行できていない
（`eslint.config.js` の require が解決しない）。CI 新設（P3-1）の際にまとめて回す。
