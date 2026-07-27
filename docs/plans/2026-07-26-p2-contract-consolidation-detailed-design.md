# P2 詳細設計: 契約の一本化 5 件

ステータス: 実装済み（詳細設計 + 実装で確定した差分を §9 に反映）
入力: [`2026-07-26-open-items-and-concerns.md`](2026-07-26-open-items-and-concerns.md) §7.3 / §6.2
参照: [P0 詳細設計](2026-07-26-p0-pre-canary-fixes-detailed-design.md)（構造テストの流儀・除外リストの作法） /
[P1 詳細設計](2026-07-26-p1-config-and-safety-detailed-design.md) §3.1.2（**解決済みの文を入力で渡し、受け側の表は受け皿へ降格**する手） /
[S1 詳細設計](2026-07-26-s1-config-two-layer-detailed-design.md)（設定 2 層） /
[S3/S2 詳細設計](2026-07-26-s3-s2-node-repos-and-cowork-roots-design.md)（`repos[].local` の置き場） /
[S8/S9-4 詳細設計](2026-07-26-s8-s9-4-board-ui-and-doctor-chat-detailed-design.md) §5・§6（板 UI とノード宣言） /
[委譲公示板 設計](2026-07-23-delegation-board-distributed-bidding-design.md)（板の正典設計） /
[常駐一本化 設計](2026-07-24-single-resident-controller-design.md) §9 C13（フリート更新の規律）
実装フェーズ: **静止点で全ノード一斉**。R2b（ノード直轄実行）の設計より**前**。

---

## 1. スコープ

やること（総覧 §7.3 の 5 行）:

| # | 直すもの | 何が壊れているか | 規模 |
|---|---|---|---|
| P2-1 | `CONTRACT_VERSION` の 3 重定義 | 片方だけ上げると「版 2 と宣言しつつ版 1 で判定」＝**無言の不参加** | S |
| P2-2 | 板への `local` publish とスキーマの矛盾 | S3 の動機（ホスト固有の絶対パスを配らない）と正面から矛盾。`$defs.node.repos` の宣言形も実装と違う | S |
| P2-3 | `workloads` / `max_concurrent` を入札判定が読まない | 宣言と判定が食い違う。`max_concurrent: 0` の意味がスキーマと実装で真逆 | M |
| P2-4 | `BoardRepo` 請負側書き込みの排他漏れ | 再クローン（`rmtree`）や `pull --rebase` と競合すると入札・中止が消えうる | S |
| P2-5 | 文字列・小物の一本化 | 同じ規則の 2〜4 実装。片方だけ直すと黙ってずれる | S |

やらないこと（スコープ外）:

- **R2b（ノード直轄実行）そのもの**。P2-3 は R2b が板の契約を固める前に「宣言と判定の
  食い違い」を消しておくのが目的で、ワーカーノードの実行経路は作らない（総覧 §2）。
- `canceled`（米式）識別子の**一括改名**。総覧 §7.3 P2-5 のとおり「触るファイルの修正時に限る」
  ——改名だけのコミットは履歴のノイズになる（§3.5.5）。
- 投機同時実行（speculation）の実装（総覧 §3 P4-d）。契約からも削除済みで、
  本設計は `results/<who>.json` の余地を塞がないことだけを守る。
- `CONTRACT_VERSION` の**値の引き上げ**。§3.1.4 のとおり本設計の契約変更はすべて
  読み手互換（additive / 情報の削減）なので据え置く。

**本設計で新たに見つけたもの**は §7 にまとめ、どの P2 項目へ織り込んだかを対応付ける。
うち §7-A は**入札が届いていない経路がもう 1 本ある**という発見で、P2-3 の中で直す。

---

## 2. 現実装の事実（実測・2026-07-26）

実測の前提: 4 パッケージとも全緑（agent-project 1,127 / agent-flow 571 / agent-amigos 176 /
agentcore 80 + 66 / dashboard `npm test`）。以下は「緑のまま壊れている」ものの棚卸し。

### 2.1 P2-1: `CONTRACT_VERSION` の 3 箇所

| 場所 | 値 | 誰が使うか |
|---|---|---|
| `agentcore/board.py:34` | `1` | `eligible()` の既定 `contract_version=`（**入札判定の正**） |
| `agent_project/resident/status.py:32` | `1` | `NodeCapability.contract_version`（板へ**宣言**する値）・`EngineStatus.contract_version`（dashboard へ宣言する値）・`_board_participate_tick` の `board.contract_version` |
| dashboard `main/engine.js:34` | `1` | `summarize()` が `status.contractVersion` と比較し「更新が必要」を出す |

`contract_compatible` は `agentcore/board.py:37` と `resident/status.py:35` に**同じ本体が
docstring ごと 2 つ**ある。後者の消費者は `resident/__init__.py` の再エクスポートと
`tests/test_resident_status.py` だけで、**製品コードから呼ばれていない**（実測）。

壊れ方は fail-close ゆえに無言になる: 板側だけ `2` に上げると、ノードは
`nodes/<id>.json` に `contract_version: 2` を宣言しつつ `eligible()` は `1` で判定し、
`requires.contract_version: 2` の公示に**入札しない**。誰も例外を見ない。

### 2.2 P2-2: 板へ配っている `local`

`_node_capability`（`resident_cli.py:422-442`）は host.yaml の `repos[]` を
**そのまま**（`local` 込みで）`NodeCapability.repos` に載せ、`nodes/<node-id>.json` として
共有 git の板へ push する。`repos.schema.json` の `local` は
「【非推奨・S3】ホスト固有なので共有レジストリには置けない（1 台で書いた値が全 PC へ配られる）」
と宣言している。

**読み手を全部数えた（実測）**:

| 読み手 | `local` を見るか |
|---|---|
| `agentcore.board.declared_repo_ids`（入札判定） | **見ない**（`name` と正規化 `url` だけ） |
| dashboard `board-adapter.listNodes` → `repoLabels` | **見ない**（`url` からラベルを作るだけ。S8-1 で明示的に落としている） |
| `doctor._node_record_is_fresh` | 見ない（`heartbeat` / `fresh_after_sec` のみ） |
| その他 | 無し（`nodes/*.json` を読むのは上の 3 者だけ） |

つまり **`local` は書かれているだけで誰にも使われていない**。S8 §6.2 が「速度最適化の
ヒント」と書いた用途（請負ノードが自分の手元クローンを使う）は、実際には
`_repolocal.merge_local`（`agent_flow/board.py:320`）が**自ノードの host.yaml から**
解決しており、板の `local` は経路に無い。

あわせて `$defs.node.repos` は `{"$ref": "repos.schema.json"}`＝レジストリ mapping 形を
宣言しているが、実装が書くのは host.yaml の**配列形**（`[{url, local}, …]`）。
スキーマ検証を掛ければ落ちる（掛けていないので誰も気付かない・§7-E）。

### 2.3 P2-3: 宣言と判定の食い違い

#### `workloads`

`board.schema.json` `$defs.node.workloads`:
> 受けられるエンジン。空 = 全部。公示の workload がこれに含まれないと入札しない

実装:

| | 宣言（publish） | 判定（bid） |
|---|---|---|
| 常駐体 | `["flow"] + (["amigos"] if host.amigos_bus else [])`（`resident_cli.py:430`） | — |
| agent-flow | — | `post["workload"] != "flow"` で弾く（`board.py:274`）。**ノードの宣言は見ない** |
| agent-amigos | — | `post["workload"] != "amigos"` で弾く（`board.py:274`）。同上 |
| `agentcore.board.eligible` | — | `workloads` の引数自体が**無い** |

各エンジンは「自分の workload しか拾わない」ので現状の実害は小さい。**害が出るのは宣言側**
——`amigos_bus` を host.yaml に書いていない PC で人が `agent-amigos daemon --board` を直接
起こすと、板には `workloads: ["flow"]` と宣言しつつ amigos の公示に入札する。
板の宣言は owner-picks の落札判断と dashboard の端末一覧の根拠なので、
**宣言が嘘をつく**形になる。

#### `max_concurrent`

| | 0 の意味 |
|---|---|
| `board.schema.json` `$defs.node.max_concurrent` | 「0/省略 = **無制限**。超過時は新規入札を控える」 |
| `HostConfig.max_concurrent`（`resident_cli.py:248`） | 未宣言も 0 も**区別せず** `0` |
| `NodeWorkerPool` への配線（`resident_cli.py:871`） | `host.max_concurrent if > 0 else 4`＝**既定 4** |
| `NodeWorkerPool.__init__`（`worker.py:52`） | `max(1, int(...))`＝**0 を無制限にできない** |
| `host.yaml.example` | `max_concurrent: 0   # 0 = 既定（4）` |
| 入札判定（`eligible`） | **読まない**（引数が無い） |

「超過時は新規入札を控える」という契約は**どこにも実装が無い**。忙しいノードが板の仕事を
掴んだまま枠待ちで塞ぎ、空きノードが拾えない。

#### 板上の「自分がいま何件持っているか」

判定材料は既に板にある——`delegations/<id>/status/<who>.json` の非終端が
「自分が落札・引き渡し済みで、まだ終わっていない」件数。読み方は 2 実装
（`agent_flow/board.py:_dispatched_by` / `agent_amigos/board.py` の同型）で、
どちらも `vocab.is_terminal` で終端を弾いている。

### 2.4 P2-4: `BoardRepo` の排他

`agent_project/board.py` のメソッドを排他の有無で並べる:

| メソッド | `_locked()` | `_ensure()` |
|---|---|---|
| `sync_pull` / `sync_push` / `write_post` / `write_node` / `drop_delegation` / `sweep_terminal_delegations` | ○ | ○ |
| **`write_bid`（209）/ `write_cancelled`（227）/ `write_award`（239）** | **×** | **×** |
| `read_*` / `has_post` / `is_terminal` / `has_live_bid` | ×（読みなので可） | × |

3 つとも S8（手動入札・委任中止・落札）で足したもので、既存の書き込みメソッドと
規律が揃っていない。競合相手は実在する:

- `transport._reset_clone_dir`（`transport.py:313`）——破損検知時の `rmtree`。
  `ensure_clone` の中で走り、**`sync_pull` / `sync_push` の内側**なので `_locked()` を持つ。
  ロックを取らない `write_cancelled` はこれと並走できる。
- `sync_push` の `pull --rebase` → 再 push（作業ツリーを動かす）。
- 依頼側の `_act_batch`（ThreadPoolExecutor）が同じ `BoardRepo.dir` を叩く経路。

`write_json_atomic` は `os.makedirs` を含むので**単体では動く**（テストが緑な理由）。
壊れるのは並走したときだけで、**手動入札は人が押した 1 回きり**なので再現しにくい。

**再入の注意**: `_locked()` は `fcntl.flock` で、同一プロセスが別の fd で同じロックを
取ると自分自身と競合する。`write_bid` の中で `BoardRepo.sync_push` を呼んではいけない
（現行も呼んでいない——push は `_ingest_node_commands` が外側で 1 回だけ行う）。
`sweep_terminal_delegations` が `self._transport.sync_push`（`BoardRepo` のではなく
transport の）を `_locked()` の中で呼んでいるのが既存の作法。

### 2.5 P2-5: 小物の重複

| 重複しているもの | 実装 | 差 |
|---|---|---|
| `DIFF_CRITERION` | `verify.py:194` / スキル `prompt.py:43` | 現在は同文。片方だけ直すと**レポートの基準文とエージェントが見た基準文**が黙ってずれる（判定は番号で突き合わせる） |
| 退避の指示文 | `agent_flow/agent.py:607` / `agent_amigos/agentcli.py:107` / `agent_project/prioritize.py:_SPILL_INSTRUCTION` | 「何の全文か」だけが違い、**枠の文は同じ**。定義側 `spill.instruction`（`agents/kiro.json`）は別物（権限フラグ置換を伴う読み取り専用向け・P1 §7-A/B） |
| repos 宣言の正規化 | `agentcore.repolocal.normalize_repos` / `resident_cli._normalize_host_repos`（§7-B） | mapping+dict 形での `None` の扱いが違う（後者は `"None"` という文字列になる） |
| URL 正規形 | `repolocal.normalize_repo_url`（Python） / `nodeRepos.normalizeRepoUrl`（JS） | **symlink 解決の有無**（Python は `Path.resolve()`＝解決する / JS は `path.resolve()`＝しない）。`repos:` 行末コメントの件は**既に解消済み**（`base/main/yaml.js` へ移行済み・§7-F） |
| ファイル名サニタイズ | `protocol.safe_name` / `agent_project/board.py:_safe_node` / `agent_flow/gitbus.py:_safe` / `agent_amigos/board.py:_safe` | 前 2 者は**同一実装**。flow だけ置換文字が `_`（他は `-`）（§7-D） |
| `NodeCapability.write` のパス導出 | `status.py:80` が `f"{self.node}.json"` を直書き | `BoardRepo.node_path` は `_safe_node` を通す。`node` は正規化済みなので現経路では同値 |

---

## 3. 設計

### 3.1 P2-1 — `CONTRACT_VERSION` の正典を `agentcore.board` に置く

#### 3.1.1 Python 側は import に落とす

```python
# agent_project/resident/status.py
from agentcore.board import CONTRACT_VERSION, contract_compatible  # noqa: F401
# ノード契約バージョンの正典は agentcore.board（入札判定が使う値と同一であること自体が
# 契約）。ここに 2 つ目の定数を置くと「版 2 と宣言しつつ版 1 で判定する」が作れてしまい、
# 入札選別は fail-close なので**誤動作ではなく無言の不参加**として出る（設計 §9 C13）。
```

- `contract_compatible` の**本体は削除**（docstring ごと）。`resident/__init__.py` の
  再エクスポートと `__all__` は**残す**——`from agent_project.resident import
  contract_compatible` を書いている既存テストの入口を変えない。
- `resident/status.py` は既に agentcore を `sys.path` へ入れているので（`status.py:22-26`）、
  import 経路の追加工事は不要。

#### 3.1.2 dashboard（JS）は定数を残し、Python とゴールデンで縛る

JS から Python の値を実行時に引くのは筋が悪い（画面の判定のたびにプロセス起動になる。
`nodeRepos.js` が JS で読み直しているのと同じ理由）。定数は残し、**テストで一方向に固定する**:

```js
// test/contract-version-golden.test.js
// ノード契約バージョンの正典は Python（agentcore/board.py の CONTRACT_VERSION）。
// この画面の EXPECTED_CONTRACT_VERSION は写しなので、正典を上げたらここが落ちる。
// 逆向き（JS だけ上げる）も落ちる——値が「一致すること」だけを検査する。
const py = fs.readFileSync(path.join(ROOT, 'tools/agent-tools/agentcore/agentcore/board.py'), 'utf8');
const m = /^CONTRACT_VERSION\s*=\s*(\d+)\s*$/m.exec(py);
assert.ok(m, 'agentcore/board.py の CONTRACT_VERSION を読めない（正典が動いた？）');
assert.strictEqual(engine.EXPECTED_CONTRACT_VERSION, Number(m[1]));
```

`agent-cli-golden.test.js` は期待値を**両側に書き写す**流儀だが、こちらは**正典を実際に
読む**形にする——CLI 定義の golden は「同じ入力から同じ出力が出る」ことの検査で写しに
意味があるが、バージョン番号は写しそのものが問題なので、写しを機械が突き合わせる。

#### 3.1.3 `EngineStatus` と `NodeCapability` が同じ番号を名乗ることの明文化

現在この 1 つの数が 2 つの面（板の `nodes/<id>.json` と `engine/status.json`）の版を
兼ねている。**兼ねたまま**にする（分けると「板は互換だが画面は非互換」という状態が生まれ、
利用者に見せる文言が 2 系統になる）。ただし**それは意図であって偶然ではない**ので、
`agentcore/board.py` の定数コメントへ 1 行足す:

```python
# この数は 2 つの面の版を兼ねる: 板の `nodes/<node-id>.json`（入札の語彙）と
# `.agents/engine/status.json`（画面の語彙）。分けないのは、フリート更新を静止点で
# 一斉に行う規律（C13）の下では「板だけ新しい」状態を作らないため——分けた瞬間に
# 「板は互換だが画面は非互換」という中間状態が正当になり、更新漏れの説明が 2 系統になる。
```

#### 3.1.4 本設計の契約変更で版を上げない理由

P2 が板へ与える変更は 3 つで、いずれも**読み手互換**:

| 変更 | 旧ノード（版 1）から見ると |
|---|---|
| `nodes/<id>.json` から `repos[].local` を落とす（P2-2） | 誰も読んでいない項目が消えるだけ |
| `workloads` を未宣言なら出さない（P2-3） | 「空 = 全部」の語彙どおりに読める |
| `max_concurrent` の 0 を無制限として扱う | 判定に使っていなかった項目に意味が付くだけ（新ノード同士でのみ効く） |

よって `CONTRACT_VERSION` は **1 のまま**。上げると全ノードが一斉に入札不能になる窓を
自分で作ることになる（それこそ C13 が静止点を要求している理由）。

### 3.2 P2-2 — 板へ `local` を publish しない

#### 3.2.1 決め: publish をやめる（総覧の推奨どおり）

§2.2 で数えたとおり **`local` の読み手は 1 人も居ない**。入札可否は url ベースで足り
（S3-5 の設計どおり `local` はヒント）、落札後の worktree 切り出しは請負ノードが
**自分の** host.yaml から解決している（`_repolocal.merge_local`）。他 PC の絶対パスを
共有リポジトリへ配る必然性が無い。

```python
def _board_repo_declaration(repos: "list[dict]") -> "list[dict] | None":
    """host.yaml の `repos[]` → 板の `nodes/<id>.json` に載せる形。

    **`local` は落とす**（S3）。あれはこの PC にしか存在しない絶対パスで、板は共有
    リポジトリ＝置いた瞬間に全 PC へ配られる。`repos.schema.json` が `local` を
    「共有レジストリには置けない」と宣言しているのと同じ理由がそのまま当たる。

    速度最適化のヒントとしての `local` は**請負ノードが自分の host.yaml から**解決する
    （`agentcore.repolocal.merge_local`）ので、板を経由する必要がそもそも無い。
    入札判定（`agentcore.board.declared_repo_ids`）が見るのは name と url だけ。
    """
    out = [{k: v for k, v in r.items() if k != "local"} for r in repos]
    return [r for r in out if r.get("url")] or None
```

`local` を落とした結果 `url` だけのエントリになるので、**url を持たない宣言は落とす**
（載せても照合に使えず、読み手に空のラベルを出させるだけ）。

#### 3.2.2 スキーマ側（`board.schema.json` `$defs.node.repos`）

宣言を実装へ合わせる。**2 形を受ける**（レジストリ mapping 形＝amigos の設定由来 /
配列形＝host.yaml 由来）ことと、**`local` を載せない**ことを機械が読める形で書く:

```json
"repos": {
  "description": "担当リポジトリの宣言。2 形を受ける: repos.schema.json のレジストリ mapping（agent-amigos の設定由来）と、host.yaml の repos[] 由来の配列（url のみ）。公示の workspace.url / requires.repos を URL 正規化で照合し、担当していれば入札対象。readonly エントリは書込先候補にしない。**local は載せない** — ホスト固有の絶対パスは共有リポジトリである板に置けない（repos.schema.json の local と同じ理由）。手元クローンの解決は各ノードが自分の host.yaml から行う（agentcore.repolocal）",
  "oneOf": [
    {"$ref": "repos.schema.json"},
    {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["url"],
        "properties": {"url": {"type": "string"}},
        "not": {"required": ["local"]},
        "additionalProperties": true
      }
    }
  ]
}
```

`repos.schema.json` 側は**触らない**——publish をやめれば矛盾が消えるので、
deprecated の文言を弱める必要が無い（総覧 P2-2 が示した 2 案のうち、
文言改訂が要るのは「維持する」判断を採ったときだけ）。

#### 3.2.3 移行

板に既にある `nodes/<id>.json` は、次の board tick で**内容が変わる**ので即座に
書き直される（`write_node` は heartbeat 以外の差分があれば間隔を無視して書く・
`board.py:198-204`）。古い `local` 付きレコードは自然に消える。**掃除の手順は不要**。

### 3.3 P2-3 — 宣言と判定を噛み合わせる

#### 3.3.1 `eligible()` に 2 つの引数を足す（純関数のまま）

`agentcore.board.eligible` の docstring は「判定材料はすべて引数で受ける」と宣言している。
その方針のまま:

```python
def eligible(post: dict, *, repos=None, tags=None, agent_cli=None, workloads=None,
             contract_version: "int | None" = CONTRACT_VERSION,
             max_concurrent: "int | None" = None, inflight: int = 0) -> bool:
```

| 引数 | 規則 | 出典 |
|---|---|---|
| `workloads` | 空/None は**無制限**（schema の「空 = 全部」）。非空なら `post["workload"]` が含まれること | `board.schema.json` `$defs.node.workloads` |
| `max_concurrent` / `inflight` | `max_concurrent` が None または 0 なら無制限。正の値で `inflight >= max_concurrent` なら入札しない | 同 `$defs.node.max_concurrent` |

`workloads` だけ fail-**open**（空 = 全部）なのは schema の語彙そのままで、`agent_cli` の
fail-close（宣言が空なら要求のある公示に入札しない）と非対称になる。**この非対称は意図**
なのでコメントに残す:

```python
    # tags / agent_cli / contract_version は公示が「要る」と言った条件で、宣言の欠落は
    # 「無い」と読む（fail-close）。workloads はノードが「これしかやらない」と言う条件で、
    # 宣言の欠落は「制限しない」と読む（fail-open）。向きが逆なのは、要求の欠落と
    # 制限の欠落では安全な倒し方が逆だから——要求を無視すると拾えないノードが拾い、
    # 制限を強制すると宣言していないノードが全部止まる。
```

#### 3.3.2 `workloads` の宣言を「明示だけ」にする（決め）

現在の publish（`amigos_bus` からの導出）は**制限として使えない**。`amigos_bus` を書いて
いない PC でも人が amigos の板参加を起こせるので、導出値を判定に使うと**動いていた
ノードが黙って入札をやめる**（P2 でいちばん起こしてはいけない壊れ方）。

決め: **`workloads` は host.yaml の明示宣言だけを正とし、未宣言なら板に出さない**。

```yaml
# host.yaml（追加・任意）
workloads: [flow]      # このノードが引き受けるエンジン。省略 = 制限しない（全部）
```

- `HOST_TOP_KEYS`（P1-3）へ `workloads` を追加。型は `_str_list` を通す
  （スカラ救済も `tags` / `agent_cli` と同じ・W6-a）。
- `_node_capability` は `workloads` を**宣言があるときだけ**載せる。
  `NodeCapability.to_dict` は現在 `workloads` を無条件に出しているので、
  `tags` / `agent_cli` と違い**空なら省く**側へ寄せる（「空 = 全部」の語彙は
  「キーが無い」と同義なので、出さない方が宣言の意図に近い）。
- 解決は 1 関数に置き、publish と判定の両方がこれを呼ぶ:

```python
# agentcore/board.py
def declared_workloads(host: dict) -> "list[str]":
    """host.yaml のノード宣言 → 引き受けるエンジンの一覧（空 = 制限しない）。

    **導出しない**（`amigos_bus` の有無から推測しない）。導出値は「板へ出す宣言」としては
    もっともらしいが、判定に使うと `amigos_bus` を書かずに amigos の板参加を起こしている
    PC が黙って入札をやめる。宣言していないものを宣言として配ると、owner-picks の落札判断と
    端末一覧が嘘を読むことになる——出さないのが正しい（空 = 全部）。
    """
```

**失うもの**: dashboard の端末一覧から「この PC は flow/amigos のどちらをやるか」の表示が
（宣言するまで）消える。**それでよい**——今そこに出ていたのは host.yaml が言っていない
ことなので、消えるのは嘘であって情報ではない。宣言すれば戻る。
`host.yaml.example` とセットアップガイド §6 に 1 行足して、宣言を促す。

#### 3.3.3 `max_concurrent` の `0` をスキーマへ寄せる

「未宣言」と「明示の 0」を区別する（これができないと `0 = 無制限` を実装できない）:

```python
# HostConfig.__init__
budget = data.get("budget") or {}
raw = budget.get("max_concurrent") if isinstance(budget, dict) else None
# **未宣言（None）と明示の 0 を区別する。** 0 はスキーマ上「無制限」で、未宣言は
# 「既定に従う」。両方を 0 に潰していたので、無制限を書く手段が無く、しかも
# 板の契約（0 = 無制限）と実装（0 = 既定 4）が真逆になっていた。
self.max_concurrent: "int | None" = int(raw) if isinstance(raw, (int, float)) else None
```

| 宣言 | 板の入札自己抑制 | `NodeWorkerPool` |
|---|---|---|
| 未宣言（None） | 抑制しない | **4**（従来の既定。旧 flow daemon の `--max-workers` 由来） |
| `0` | 抑制しない（無制限） | **上限なし** |
| `n > 0` | 板上の自分名義の非終端が `n` 以上なら入札しない | `n` |
| 負値 | 未宣言と同じ（`host_config_findings` に所見 1 件・W6） | 同左 |

`NodeWorkerPool` は「上限なし」を表現できるようにする:

```python
    def __init__(self, max_concurrent: "int | None", ...):
        """`max_concurrent` は None で「上限なし」。0 も同義（板の語彙に合わせる）。

        以前は `max(1, int(...))` で 0 を 1 に潰しており、板が `0 = 無制限` と宣言している
        のに実装だけ真逆（0 = 既定 4）だった。**「未宣言なら 4」は呼び出し側の既定**で、
        プールの仕事ではない——ここが既定を持つと、宣言を読む場所が 2 つになる。"""
        n = None if max_concurrent is None else int(max_concurrent)
        self._max = None if (n is None or n <= 0) else n
```

`_used(...) >= self._max` の比較は `self._max is not None and ...` へ。
`status()["max_concurrent"]` は**板の語彙で出す**（上限なしは `0`）——
`engine/status.json` を読む dashboard が別の語彙を覚えなくて済む。

> **無制限は足を撃ちうる**。それでも実装をスキーマへ寄せるのは、契約が先に
> 「0 = 無制限」と宣言してしまっているから（読み手が既にそう読む）。危険は
> `host.yaml.example` の注記と doctor の設定値検査（P3-3 の積み残し 2）で扱う。

#### 3.3.4 「自分がいま板で何件持っているか」の 1 実装

```python
# agentcore/board.py
def node_inflight(board_root: str, node_id: str) -> int:
    """板の上で、このノードが落札・引き渡し済みでまだ終端していない委譲の件数。

    根拠は板の `delegations/<id>/status/<who>.json`（自分が引き受けた印）で、自分のバスや
    プロセス内カウンタではない——同じノードで 2 つのプロジェクトが同じ板を巡回する構成が
    あり、プロセス内で数えると片方の分が見えない（`_dispatched_by` が板を見る理由と同じ）。

    終端の読みは `vocab.is_terminal_read`（旧綴り `canceled` も終端として読む）。板には
    語彙統一より前のノードが書いた値が残りうるので、**読みは寛容・書きは正典のみ**。
    """
```

- 置き場が `agentcore.board` なのは、板のレイアウトを知る実装が既に 3 つある
  （flow `_dispatched_by` / amigos の同型 / project `is_terminal`）ため。
  ここが 4 つ目にならないよう、**flow / amigos の `_dispatched_by` もこの関数へ寄せる**
  （1 件判定は `node_inflight` の内側の述語を公開して共有する）。
- `agentcore.board` は今まで純関数だけだったが、`agentcore.protocol` は既に fs を触る
  （`renew_lease` / `winner`）。モジュールの性格から外れない範囲——**規則（純関数）と
  板レイアウトの読み取りを、ファイル内で節に分けてコメントで明示する**。

#### 3.3.5 呼び出し側の配線

```python
# agent_flow/board.py（agent-amigos も同型）
def board_eligible(post, node_repos, node_tags, node_agent_cli=None, *,
                   node_workloads=None, max_concurrent=None, inflight=0) -> bool:
    return _boardrules.eligible(post, repos=node_repos, tags=node_tags,
                                agent_cli=node_agent_cli or [],
                                workloads=node_workloads or [],
                                max_concurrent=max_concurrent, inflight=inflight)
```

- `_node_declaration`（flow）は `workloads` と `budget.max_concurrent` も返す
  （host.yaml が正典・`board_repos` 等と同じ降格の形）。
- **`inflight` は poll_board のループの外で 1 度だけ数える**（委譲ごとに板を走査すると
  O(n²) になる）。ループ内で落札するたびに `+1` する——同じ 1 巡で上限を超えて拾わない。
- **手動入札（`forced`）は自己抑制も飛ばす**。`_has_own_live_bid` が真なら
  `eligible` を問い直さない既存の規則（`board.py:289-291`）に `max_concurrent` も含める
  ——人が「このノードで請け負う」を押したなら、自己抑制は人が上書きしたということ。
- 常駐体の `_ingest_node_commands`（手動入札の書き手）も同様に抑制しない。

#### 3.3.6 **amigos が `agent_cli` を渡していない**（§7-A・P2-3 で直す）

`agent_amigos/board.py:272` は `board_eligible(post, node_repos, node_tags)` で、
第 4 引数（`node_agent_cli`）を渡していない。`eligible` の CLI 判定は fail-close
（`need_cli and not (need_cli & set())` → False）なので、**`requires.agent_cli` を持つ
公示に amigos ノードは永久に入札しない**。`daemon.agent_cli`（スカラ）は存在するので、
渡すだけで直る:

```python
        if not board_eligible(post, node_repos, node_tags,
                              [daemon.agent_cli] if daemon.agent_cli else [],
                              node_workloads=..., max_concurrent=..., inflight=...):
```

`daemon.agent_cli` は**スカラ 1 件**（`--agent-cli` / 設定）で、flow / 常駐体が扱う
「使える CLI の一覧」とは粒度が違う。`assign.py:141` が既に
`[self.agent_cli] if self.agent_cli else []` の形で畳んでいるので、同じ流儀に揃える。

### 3.4 P2-4 — 請負側書き込みを他のメソッドと揃える

```python
    def write_bid(self, did: str, node_id: str, lease: float, workload: str = "flow") -> bool:
        with self._locked():
            self._ensure()
            bids = os.path.join(self.delegation_dir(did), "bids")
            return _protocol.renew_lease(bids, node_id, lease, extra={"workload": workload})

    def write_cancelled(self, did: str, reason: str, by: str) -> str:
        with self._locked():
            self._ensure()
            ...

    def write_award(self, did: str, node: str, by: str) -> str:
        with self._locked():
            self._ensure()
            ...
```

- **`_locked()` の中で `BoardRepo.sync_push` を呼ばない**（同一プロセスの別 fd で同じ
  flock を取ると自分と競合する）。push は呼び出し側（`_ingest_node_commands`）が
  外側で 1 回だけ行う現行のままにする。この制約はクラスの docstring へ 1 行残す。
- `renew_lease` は内部で別のロック（`agentcore-claim-locks`）を取る。ロックの入れ子は
  **board → claim の一方向だけ**（逆順の経路はコードベースに無い）。順序を固定する旨も
  docstring に書く——将来 claim 側から板を触る実装が入ると deadlock になる。
- 排他が要る理由（`rmtree` を伴う再クローンとの競合）は §2.4 の事実をそのまま
  コメントへ。「なぜ読みは要らないか」（読みは壊れても None に倒れる）も併記する。

**戻り値の扱い**（§7-H）: `write_bid` は `renew_lease` の戻り（lease がまだ十分なら
False）をそのまま返す。`_ingest_node_commands` は現在これを捨てて必ず
「入札しました」と受理レシートを書いている。**レシートの文言を戻り値で分ける**:

```python
            wrote = board.write_bid(...)
            detail = (f"{host.node_id} が入札しました" if wrote
                      else f"{host.node_id} の入札は既に有効です（延長不要）")
```

冪等であることは変わらない（二度押しでもエラーにしない）が、「押したのに何も
書かれていない」が観測できるようになる。

### 3.5 P2-5 — 文字列・小物の一本化

#### 3.5.1 `DIFF_CRITERION`（P1-1 で実証済みの手をそのまま）

本体を正典にし、スキルは**解決済みの文を受け取る**。`side_effects_text` と同じ additive:

```python
# verify.py — verifier_input へ 1 キー
        "diff_criterion": DIFF_CRITERION,
```

```python
# .github/skills/backlog-verifier/scripts/prompt.py
DIFF_CRITERION = "…"   # 入力に diff_criterion が無いときの受け皿（スキル単体実行・旧呼び出し側）
...
    criteria = acceptance + [str(spec.get("diff_criterion") or "").strip() or DIFF_CRITERION]
```

- スキル側の定数は**残す**（スキルは単体でも動く契約）。ずれても本体の値が勝つので、
  実害のある重複ではなくなる。`SKILL.md` の入力表へ 1 行。
- `verify.py:551`（レポートの基準列を組む側）も同じ `DIFF_CRITERION` を使っているので、
  「レポートの基準文」と「エージェントが見た基準文」が構造的に一致する。

#### 3.5.2 退避の指示文（`spill_prompt`）— 枠だけを 1 か所へ

3 者の文は「何の全文か」だけが違い、枠（「以下のファイルに〜があります。必ずファイルの
内容を読み込み、その指示に従って〜ください: {file}」）は同じ。**枠を関数にする**:

```python
# agentcore/agentcli.py
def spill_instruction(what: str, *, then: str = "その指示に従ってください") -> str:
    """argv 退避時に本文の代わりに渡す短い指示（`{file}` を含む）。

    枠だけをここに置き、`what`（何の全文か）は呼び出し側が決める——役割ごとに違うのは
    そこだけで、「必ず読み込ませる」という**効き目に関わる部分は共通**。3 者が全文を
    自前で持つと、効き目の悪い言い回しの修正が 1 か所にしか入らない。

    **定義側の `spill.instruction`（`agents/<cli>.json`）とは別物**（P1 §7-A/B）。
    あちらは権限フラグの置き換えを伴う読み取り専用の退避モード用で、Python からは
    使われていない。名前が似ているので、この関数の呼び出し側は必ず `spill_prompt` 経由。
    """
    return (f"以下のファイルに{what}があります。"
            f"必ずファイルの内容を読み込み、{then}: {{file}}")
```

呼び出し側は `what` だけ渡す:

| 呼び出し側 | `what` | `then` |
|---|---|---|
| agent-flow | `"このタスクの全文（依存タスクの成果物を含む）"` | `"その指示に従ってタスクを実行してください"` |
| agent-amigos | `"このターンの全文（役割・ミッション・新着メッセージを含む）"` | 既定 |
| agent-project | `"この処理の入力の全文"` | `"その内容を対象にしてください"` |

既存テストは文言を固定していない（実測）ので、移設で落ちるテストは無い。
**定義側 `spill.instruction` へ寄せる案は採らない**——用途が違う（権限置換を伴う）ことは
P1 §7-A で確定済みで、寄せると退避のたびに検証が実行権限を失う。この決着を
`docs/designs/agent-cli-plugin-design.md` の spill の項へ 1 行で残す（P1 の記述を確定形に）。

#### 3.5.3 repos 宣言の正規化を 1 実装へ（§7-B）

`resident_cli._normalize_host_repos` を削除し、`agentcore.repolocal.normalize_repos` を呼ぶ。
差は mapping+dict 形での `None` の扱いだけ（現行の resident 版は `local: None` を
`"None"` という文字列にする＝存在しないパスを宣言したことになる）。repolocal 版が正しい。

#### 3.5.4 URL 正規形の JS/Python 差（symlink）

JS 側 `normalizeRepoUrl` のローカルパス分岐を、Python の `Path.resolve()` と揃える:

```js
    const expanded = s.replace(/^~(?=$|\/|\\)/, os.homedir());
    try {
      // Python 側は Path.resolve()＝**symlink も解決する**。ここが path.resolve だけだと、
      // 同じクローンを symlink 経由で宣言した PC で「同じリポジトリ」と読めない
      // （症状は「なぜかローカルクローンが使われない」＝速度が出ない理由が分からない形）。
      return fs.realpathSync.native(expanded).toLowerCase();
    } catch {
      // 実体が無いパスは解決できない。Python の resolve(strict=False) と同じく
      // 絶対化だけして返す。
      return path.resolve(expanded).toLowerCase();
    }
```

**両実装をゴールデンで縛る**（`agent-cli-golden` と同じ流儀。ただし symlink を作る
ケースは一時ディレクトリで両言語が同じ表を回す）:

- Python: `agentcore/tests/test_repolocal.py` に `NORMALIZE_GOLDEN` の表を置く。
- JS: `test/repo-url-golden.test.js` が同じ表（url 系のみ・fs に依存しない項目）を回す。
  symlink 解決は「解決すること」を両側の個別テストで確かめる（表には載せない——
  一時パスは決定的でないため）。

#### 3.5.5 サニタイザとパス導出（§7-D も含む）

| 直すもの | 直し方 |
|---|---|
| `NodeCapability.write` のパス導出 | `agentcore.protocol.safe_name` を通す（`BoardRepo.node_path` と同じ規則になる） |
| `agent_project/board.py:_safe_node` | 削除し `protocol.safe_name` を使う（**同一実装**。実測で文字ごとに一致） |
| `agent_flow/gitbus.py:_safe` | **触らない**（バスのパス全般に使われており、置換文字を変えると既存 run のディレクトリ名が変わる＝静止点の意味が変わる）。代わりに `agent_flow/board.py` の**板レイアウト**に使っている 3 箇所（`_dispatched_by` / `_has_own_live_bid` / status 書き出し）を `protocol.safe_name` へ寄せる——板の名義は板の規則で綴る |
| `agent_amigos/board.py:_safe` | 同上（`protocol.safe_name` へ寄せる。実装は同一なので挙動不変） |
| `canceled`（米式）識別子 | **本設計では触らない**。上の変更で触るファイルに現れるものだけ改名する |

flow の板レイアウト側を揃える理由は §7-D: 現在 flow は入札を `protocol.renew_lease`
（`safe_name`＝`-` 置換）で**書き**、`_has_own_live_bid` は `_safe`（`_` 置換）で**読む**。
正規化済み node_id なら同値だが、**§7-C（明示 `--node-id` が正規化を通らない）と重なると
綴りが割れる**——手動入札の受け皿が永久に効かなくなる。

---

## 4. 変更ファイル一覧

| # | ファイル | 変更 |
|---|---|---|
| P2-1 | `tools/agent-tools/agentcore/agentcore/board.py` | `CONTRACT_VERSION` のコメントへ「2 つの面を兼ねる」明文化 |
| P2-1 | `tools/agent-project/agent_project/resident/status.py` | `CONTRACT_VERSION` / `contract_compatible` を agentcore から import（重複本体を削除） |
| P2-1 | `tools/agent-dashboard/test/contract-version-golden.test.js` | 新設（Python の正典を読んで JS 定数を縛る） |
| P2-2 | `tools/agent-project/agent_project/resident_cli.py` | `_board_repo_declaration()` 新設・`_node_capability` から `local` を落とす |
| P2-2 | `tools/agent-project/agent_project/resident/status.py` | `NodeCapability.to_dict` の `workloads` を「空なら省く」へ（P2-3 と同じ変更） |
| P2-2 | `schemas/board.schema.json` | `$defs.node.repos` を 2 形（mapping / 配列）へ・`local` 禁止を `not.required` で表明 |
| P2-3 | `tools/agent-tools/agentcore/agentcore/board.py` | `eligible()` へ `workloads` / `max_concurrent` / `inflight`・`declared_workloads()` / `node_inflight()` 新設 |
| P2-3 | `tools/agent-project/agent_project/resident_cli.py` | `HOST_TOP_KEYS` へ `workloads`・`HostConfig.workloads` / `max_concurrent`（None 可）・`_node_capability` の宣言化・`NodeWorkerPool` への配線 |
| P2-3 | `tools/agent-project/agent_project/resident/worker.py` | `max_concurrent=None` で上限なし・`status()` を板の語彙（0 = 無制限）で出す |
| P2-3 | `tools/agent-flow/agent_flow/board.py` | `_node_declaration` が workloads / max_concurrent も返す・`poll_board` で inflight を 1 度数えて配線・`_dispatched_by` を agentcore へ |
| P2-3 | `tools/agent-amigos/agent_amigos/board.py` | 同上 + **`agent_cli` を渡す**（§7-A の修正） |
| P2-3 | `tools/agent-project/agent-project.host.yaml.example` | `workloads:` の追加・`max_concurrent` の注記を「0 = 無制限 / 未宣言 = 4」へ |
| P2-3 | `docs/guides/single-resident-setup.md` | 板参加の節へ `workloads` を 1 行（宣言しないと制限しない） |
| P2-4 | `tools/agent-project/agent_project/board.py` | `write_bid` / `write_cancelled` / `write_award` を `_locked()` + `_ensure()` へ・ロック順序と「内側で sync_push を呼ばない」を docstring へ |
| P2-4 | `tools/agent-project/agent_project/resident_cli.py` | `write_bid` の戻り値を受理レシートの文言へ反映 |
| P2-5 | `tools/agent-project/agent_project/verify.py` | `verifier_input` へ `diff_criterion` |
| P2-5 | `.github/skills/backlog-verifier/scripts/prompt.py` / `SKILL.md` | `diff_criterion` を優先（無ければ従来）・入力表へ 1 行 |
| P2-5 | `tools/agent-tools/agentcore/agentcore/agentcli.py` | `spill_instruction()` 新設 |
| P2-5 | `tools/agent-flow/agent_flow/agent.py` / `tools/agent-amigos/agent_amigos/agentcli.py` / `tools/agent-project/agent_project/prioritize.py` | 退避指示を `spill_instruction()` 経由へ |
| P2-5 | `tools/agent-project/agent_project/resident_cli.py` | `_normalize_host_repos` を削除し `repolocal.normalize_repos` へ |
| P2-5 | `tools/agent-dashboard/src/features/agent-project/main/nodeRepos.js` | symlink 解決を Python と揃える |
| P2-5 | `tools/agent-project/agent_project/resident/status.py` / `agent_project/board.py` / `agent_flow/board.py` / `agent_amigos/board.py` | 板レイアウトの名義を `protocol.safe_name` へ寄せる |
| P2-5 | `docs/designs/agent-cli-plugin-design.md` | 2 つの「退避」の決着（定義の `spill` へは寄せない）を確定形で記述 |
| — | `docs/designs/agent-project-design.md` | 設計正典への反映: 板へ配る宣言の範囲（`local` を出さない）・`workloads` は明示宣言・`max_concurrent` の 0 の意味 |
| — | `docs/plans/2026-07-26-open-items-and-concerns.md` | §7.3 に本設計へのリンク・§6.2 の該当行に決着の追記 |
| — | `CHANGELOG.md` | 「agent-project / agentcore: 契約の一本化（P2）」 |

---

## 5. テスト計画

### 5.1 P2-1

| テスト | 何を固定するか |
|---|---|
| `test_contract_version_has_one_definition`（新規・構造） | `agent_project.resident.CONTRACT_VERSION is agentcore.board.CONTRACT_VERSION` かつ `contract_compatible` が**同一関数オブジェクト**（写しを作ったら落ちる） |
| `contract-version-golden.test.js`（新規・JS） | `engine.EXPECTED_CONTRACT_VERSION` が `agentcore/board.py` の値と一致 |
| 既存 `test_resident_status.py` | **無改変で緑**（import 経路が変わっても公開名は同じ） |

### 5.2 P2-2

| テスト | 内容 |
|---|---|
| `test_node_capability_does_not_publish_local`（新規） | host.yaml に `repos: [{url, local}]` → 板の `nodes/<id>.json` の `repos` に `local` が**無い**・`url` は在る |
| `test_local_declaration_still_reaches_the_workspace`（新規・回帰） | 同じ host.yaml で `repolocal.merge_local` が `local` を解決する（板から消しても速度最適化は効く＝消してよい根拠） |
| `test_writes_node_capability_with_local_clone_declaration`（既存） | **書き換える**（`local` を期待していた行を「`local` を出さない」へ）。名前も `..._with_repo_urls` へ |
| 入札の回帰 | `local` 抜きの宣言で `eligible` が従来どおり True（照合は url ベース） |

### 5.3 P2-3

| テスト | 内容 |
|---|---|
| `test_workloads_declared_restricts_bidding`（新規） | `workloads: [flow]` のノードは `workload: amigos` の公示に入札しない |
| `test_workloads_absent_means_all`（新規） | 未宣言なら両方に入札する（**既存ノードの挙動が変わらない**ことの担保） |
| `test_workloads_absent_is_not_published`（新規） | 未宣言なら `nodes/<id>.json` に `workloads` キーが出ない（宣言していないことを宣言しない） |
| `test_max_concurrent_zero_is_unlimited`（新規） | `budget.max_concurrent: 0` → 板の自己抑制なし・`NodeWorkerPool` に上限なし・`status()` は `0` |
| `test_max_concurrent_unset_defaults_to_four`（新規・回帰） | `budget` 自体が無い → プールは 4（従来どおり）・板の自己抑制なし |
| `test_busy_node_stops_bidding`（新規） | `max_concurrent: 1` で板に自分名義の非終端 `status/` が 1 件 → 入札しない。終端させると入札する |
| `test_manual_bid_overrides_self_throttle`（新規） | 上限に達していても、自分名義の有効な入札がある公示は取り込む（`forced` の既存規則に max_concurrent を巻き込まない） |
| `test_node_inflight_counts_only_my_unfinished`（新規） | 他ノード名義・終端済み・旧綴り `canceled` を数えない |
| `test_amigos_passes_agent_cli_to_eligible`（新規・§7-A） | `requires.agent_cli: [codex]` の公示に、`--agent-cli codex` の amigos ノードが**入札する**（現在は永久に入札しない） |
| `test_inflight_is_counted_once_per_poll`（新規） | 板走査が委譲数に比例して増えない（`node_inflight` の呼び出し回数を数える） |

### 5.4 P2-4

| テスト | 内容 |
|---|---|
| `test_bid_write_takes_the_board_lock`（新規・構造） | `write_bid` / `write_cancelled` / `write_award` の実行中に `_locked()` が保持されている（ロック取得をスパイして検査。`write_node` と同じ形） |
| `test_write_survives_a_concurrent_reclone`（新規） | `_ensure` が `rmtree` → 再クローンする状況を差し込み、`write_cancelled` が消えないこと |
| `test_no_sync_push_inside_the_lock`（新規・構造） | 3 メソッドが `BoardRepo.sync_push` を呼ばない（自己 deadlock の予防を型で固定） |
| `test_duplicate_bid_reports_already_live`（新規） | 二度押しで受理レシートの文言が「延長不要」になる（`.err` にはしない） |

### 5.5 P2-5

| テスト | 内容 |
|---|---|
| `test_skill_and_builtin_share_the_diff_criterion`（新規） | 両経路のプロンプトに `verify.DIFF_CRITERION` が現れる（P1-1 の `side_effects` テストと同じ形） |
| `test_verifier_input_carries_diff_criterion`（新規） | P0-4 流の到達検査に自動で乗る（`verifier_input` の全キー番兵） |
| `test_spill_instruction_frame_is_shared`（新規） | 3 者の指示文が `spill_instruction()` の枠を含む・`{file}` が置換される |
| flow / amigos / project の既存 spill テスト | **無改変で緑** |
| `test_host_repos_normalization_is_shared`（新規） | `HostConfig.repos` が `repolocal.normalize_repos` と同じ結果（`local: null` が `"None"` にならない） |
| `repo-url-golden.test.js` + `test_repolocal.py`（新規） | 同じ表で同じ正規形。symlink 解決は両側の個別テストで |
| `test_node_capability_path_uses_safe_name`（新規） | `NodeCapability.write` の出力パスが `BoardRepo.node_path` と一致（異常な node 名で） |

### 5.6 まとめて回す

CI（P3-1）が回す 6 系統をそのまま:

```
python3 -m unittest discover -s tools/agent-project/tests
python3 -m unittest discover -s tools/agent-flow/tests
python3 -m unittest discover -s tools/agent-amigos/tests
python3 -m unittest discover -s tools/agent-tools/agentcore/agentcore/tests
python3 -m unittest discover -s tools/agent-tools/agentcore/tests
cd tools/agent-dashboard && npm test
```

---

## 6. 実施順序と静止点

1. **P2-1 → P2-2 → P2-3** の順（依存）。P2-3 が板へ書く宣言の形を変えるので、
   P2-2（`local` を落とす）を先に済ませて `_node_capability` を 1 回だけ触る。
2. **P2-4・P2-5 は独立**。いつ入れてもよい（契約に触らない）。
3. **静止点が要るのは P2-3 だけ**。P2-1（定数の一本化）と P2-2（誰も読まない項目の削除）は
   旧ノードから見て無変化で、P2-4/P2-5 は板の語彙を変えない。P2-3 で
   **`max_concurrent` を宣言している PC の挙動が変わる**（`0` を書いていた PC は
   プールが 4 から無制限になる）ので、更新は全ノード一斉に行い、
   [node-id 切替と同じ静止点の作法](../guides/single-resident-canary.md)に倣って
   更新前に各 PC の `budget.max_concurrent` を確認する。
   - **更新前チェック**: `0` を「既定 4 のつもり」で書いている PC は、更新前に
     キーごと削るか `4` と書き直す。doctor へ所見を 1 行足す（`max_concurrent: 0` は
     無制限＝意図どおりか確認、を warn で）。
4. **`CONTRACT_VERSION` は据え置く**（§3.1.4）。上げると全ノードが一斉に入札不能になる
   窓を自分で作ることになる。
5. 完了条件: §5 のテストが緑 + 「板へ出す宣言」と「入札で読む宣言」が**同じ関数から
   出ている**ことがテストで固定されていること（P2-1 / P2-3 の構造テスト）。

---

## 7. 本設計の過程で新たに見つけたもの

総覧 §6 に無いものだけを挙げる。A・B・D・F・H は P2 の中で直す前提で §3 に織り込んである。

| # | 内容 | 重要度 | 扱い |
|---|---|---|---|
| A | **agent-amigos が `board_eligible` に `agent_cli` を渡していない**（`agent_amigos/board.py:272`）。`eligible` の CLI 判定は fail-close なので、`requires.agent_cli` を持つ公示に **amigos ノードは永久に入札しない**。`daemon.agent_cli` は存在し、`assign.py:141` は同じ値を `[x] if x else []` で畳んで使っているので、渡し忘れ。症状は総覧 §6.2 の「無言の不参加」と同型（誰も例外を見ない） | 高 | P2-3 §3.3.6 で直す。回帰テスト（§5.3） |
| B | **repos 宣言の正規化が 2 実装**。`agentcore.repolocal.normalize_repos` と `resident_cli._normalize_host_repos` が同じ規則を持ち、mapping+dict 形での `None` の扱いだけ違う（後者は `local: null` を `"None"` という**文字列のパス**にする）。`agentcore.repolocal` が「1 実装へ集約した」と宣言しているモジュールなので、集約の取りこぼし | 中 | P2-5 §3.5.3 |
| C | **明示指定の node_id が `normalize_node_id` を通らない**（agent-flow `daemon.py:84` の `args.node_id or default_node_id()` / agent-amigos `cli.py:82` の `args.node_id or settings.get("node_id") or default_node_id()`）。P0-3 は**未宣言時の導出**を 1 本にしたが、明示宣言側は素通し。agent-project の `HostConfig` は `normalize_node_id(declared)` を通すので、**同じ `node_id: DESKTOP-X` が常駐体では `desktop-x`・人が直接叩いた flow では `DESKTOP-X`** になる。正典構成（常駐体が `--node-id <正規化済み>` で子を起こす）では出ないが、人が `agent-flow participate --node-id ...` を直接叩くと板に 2 名義 | 中 | **本設計では直さない**（P0-3 と同じ「名義が変わる」変更で、静止点と切替ガイドの追記を伴う）。§8-1 として積み残しへ。D と重なると実害が出るので、D 側は P2-5 で塞ぐ |
| D | **板レイアウトの名義綴りが flow だけ別実装**。flow は入札を `protocol.renew_lease`（`safe_name`＝不正文字を `-` へ）で**書き**、`_has_own_live_bid` は `gitbus._safe`（`_` へ）で**読む**。正規化済み node_id なら同値なので現状は無害だが、C と重なると `bids/my-pc.json` を書いて `bids/my_pc.json` を読むことになり、**手動入札の受け皿（`forced`）が永久に効かない**——「押しても何も起きない」が S8-2・P0-2 に続いて 3 度目の形で出る | 中 | P2-5 §3.5.5。板レイアウトに使う 3 箇所だけ `protocol.safe_name` へ寄せる（`gitbus._safe` 自体は触らない） |
| E | **スキーマがどのテストでも検証されていない**。`jsonschema` 依存が無く、`board.schema.json` の `$defs.node.repos` が実装と食い違っていることを機械で検出する術が無い（人が読み比べて初めて分かる）。契約の正典がスキーマだと宣言しているのに、突き合わせは各ツールのテストが**手で書いた期待値**に依存している | 中 | 本設計では `$defs.node.repos` の記述だけ直す。スキーマ検証を CI へ入れるかは §8-2（P3-1 の C-4 と同じ「まず 1 回流して違反量を見る」型の判断） |
| F | **検証プロンプトの `workspace.url` が空のとき、スキルと組み込みで出力が違う**。組み込みは `ws.get('url') or '(ワークスペース)'`、スキルは `ws.get('url', '(ワークスペース)')`。`verifier_input` は `url` キーを**常に**（空文字でも）入れるので、スキル経路では「リポジトリ: 」が**空欄**になる。P1-1 が「節見出しと順序を揃える」とした意図（人が読み比べたときの差分を文章の丁寧さだけにする）の取りこぼし | 低 | P2-5 §3.5.1 と同じコミットでスキル側を `or` 形へ。テストは既存の同値テストを 1 ケース広げる |
| G | **flow / amigos が板の `status.state` を `vocab.is_terminal`（正典のみ）で読む**。語彙統一（W0-9）より前のノードが書いた `canceled` を終端と読まないので、そのノードが落札したまま消えた委譲を「実行中」と読み続ける。`vocab.is_terminal_read` が存在する理由がまさにこれ | 低 | P2-3 §3.3.4 の `node_inflight` は `is_terminal_read` で書く。既存 3 箇所の読み替えは同じコミットで（触るファイルなので） |
| H | **`write_bid` の戻り値が捨てられている**。`renew_lease` が「lease がまだ十分＝書かなかった」を返しても、受理レシートには必ず「入札しました」と書かれる。冪等性は正しいが、`board-bid` が実際に何をしたかが観測できない | 低 | P2-4 §3.4 でレシートの文言を分ける |
| I | **`NodeWorkerPool` の `status()["max_concurrent"]` が実効値（4）を返す**。`engine/status.json` 経由で dashboard が読むが、板の `nodes/<id>.json` は host.yaml の宣言値（0）を出しており、**同じ名前の項目が 2 つの面で違う数**になっている | 低 | P2-3 §3.3.3 で板の語彙（0 = 無制限）へ揃える |

---

## 8. 積み残し（本設計では扱わない）

| # | 内容 | 拾う契機 |
|---|---|---|
| 1 | ~~**明示指定 node_id の正規化**（§7-C）~~ → **決着（2026-07-27）: 正規化しない**。P0 詳細設計 §8 の「明示値はそのまま使う（cutover ガイドの約束・意図的な非対称）」と本行が正面から逆だったため、P0 側で決着した。黙って名義を書き換えず、**非正規形の明示宣言を doctor が検出**して人に切替（`doctor --node-id-cutover`）を促す形にする。この項目は静止点の相乗り一覧から外れた | 済（詳細は[棚卸し §6-2](2026-07-27-post-canary-backlog.md)） |
| 2 | **スキーマ検証を CI へ**（§7-E）。`schemas/*.json` に対する実データ（テストが作る `nodes/<id>.json` / `post.json` 等）の検証。まず 1 回流して既存の違反量を見てから必須ゲートにする | 契約の食い違いをもう 1 度踏んだとき（本設計で 2 度目） |
| 3 | **`workloads` の宣言を doctor が促す**。板に参加していて `workloads` 未宣言なら「制限しない（全部引き受ける）」ことを info で示す。宣言忘れと意図的な無制限を区別できないので、**警告にはしない** | doctor へ検査をまとめて足すとき（P3-3 の積み残し 2 と同じ回） |
| 4 | **`max_concurrent: 0`（無制限）の安全弁**。宣言どおり無制限にするが、PC を壊しうる設定なので doctor の warn を足すかは別判断 | 実際に無制限を使う運用が出てきたとき |
| 5 | **`agent_flow/gitbus.py:_safe` の置換文字**（§7-D）。板レイアウト側は P2-5 で `safe_name` へ寄せるが、バス全体のパス綴りは残る。揃えると既存 run のディレクトリ名が変わる | バス側で綴りの割れが実害を出したとき（現状は同一プロセスが書いて読むので割れない） |
| 6 | **`results/<who>.json` と `speculation`**（総覧 §3 P4-d）。本設計は `result.json` の単一確定点を前提にしたままで、投機同時実行が入るときに `node_inflight` の数え方（1 委譲 = 1 枠）を見直す必要がある | speculation を実装するとき |
| 7 | **板の `local` を消した後の速度**（P2-2）。請負ノードが自分の host.yaml に宣言していなければ、従来どおりミラー取得へ落ちる（板の `local` は元々使われていないので変化は無いはずだが、canary で「板経由の仕事が遅くなった」という申告が出たら実際の解決経路を確かめる） | canary（総覧 §1.1）で申告が出たとき |

---

## 9. 実装で確定した差分

設計と実装がずれたところ。**本文は書き換えず、ここに理由付きで残す**（P0 / P1 詳細設計と同じ流儀）。

| # | 設計 | 実装 | 理由 |
|---|---|---|---|
| 全体 | §5 のテストを足す | **その前に、テストが 1 件も走っていないファイルが 4 つあることが分かった**（§9.2）。`unittest discover` は `TestCase` サブクラスしか集めないので、関数形式で書かれた `resident` の単体テスト 31 件が CI で緑とも赤とも報告されていなかった。`_functest.module_load_tests`（`load_tests` プロトコル + `FunctionTestCase`）で拾うようにした | P2-1 / P2-3 の新規テストがまさにこの 4 ファイルへ入る。**測れない場所に護りを置いても護りにならない**ので、先に収集を直した。pytest は足さない（stdlib だけで走るのがこのリポジトリのテストの規約で、CI もそれ前提） |
| P2-1 | `resident/status.py` は import に落とす | 構造テストは**値の一致ではなく同一オブジェクト**（`is`）で見る | 値の一致だと「たまたま同じ数を 2 か所に書いた」状態が緑のまま残る。写しを作った瞬間に落ちてほしい |
| P2-2 | `local` を落とす | あわせて **url を持たないエントリも落とす** | `local` を落とすと url 無しのエントリは空の `{}` になる。照合に使えず、画面に空のラベルを出させるだけ |
| P2-3 | `NodeCapability.max_concurrent` は宣言値 | **実効値**（未宣言なら 4）を宣言する | 板の語彙に「未宣言」が無い（0 = 無制限 / n = n）。生の `None` を板へ出すと読み手が「無制限」と読むので、既定を解決してから宣言する。解決は `_effective_max_concurrent` の 1 か所で、ワーカープールへ渡す値と同じ——「板には 4 と言っておいて手元は無制限」が作れない |
| P2-3 | `workloads` は宣言があるときだけ載せる | `NodeCapability.to_dict` 側で「空なら出さない」ようにした（`tags` / `agent_cli` は従来どおり常に出す） | 非対称に見えるが理由がある。`tags` / `agent_cli` は**公示の要求と突き合わせる材料**で、空であることが fail-close の判断に効く。`workloads` は**ノードが自分に課す制限**なので、空とキー無しが同義（「宣言していない」を空配列として配ると、読み手には「宣言したうえで空」と区別が付かない） |
| P2-3 | `_node_declaration`（flow）に workloads / max_concurrent を足す | 明示上書きが揃っているときの**早期 return を外した**（常に host.yaml を読む） | `board_repos` / `board_tags` / `board_agent_cli` が全部指定されていると host.yaml を読まずに返していたが、新しい 2 つはそこに無い。CLI 側の上書きは足さない——どちらもノードの性質で、「このプロジェクトのときだけ違う」が起こらない |
| P2-3 | amigos に `agent_cli` を渡す（§7-A） | `workloads` / `max_concurrent` も **host.yaml から**読む `_node_board_declaration` にまとめた | amigos は自分の設定（`daemon.repos` / `tags`）を使うが、この 2 つはノードの宣言なので正典は host.yaml（flow と同じ判断）。`daemon.node_declaration` で明示でき、無ければ通常の探索順 |
| P2-3 | `host_config_findings` に `workloads` の型検査 | **語彙の検査も足した**（`flow` / `amigos` 以外は所見）+ `budget.max_concurrent` の型検査 | `workloads: [flwo]` は綴り間違いでも型は正しく、結果は「板の仕事を 1 つも受けない」——W6 の「無言の不参加」と同じ形なので、書いた時点で気付ける方に倒す |
| P2-4 | 3 メソッドを `_locked()` + `_ensure()` へ | ロック保持の構造テストは `_locked` を**差し替えて深さを数える**形にした | 「ロックを取ったか」だけでなく「入れ子になっていないか」も同時に固定できる（同一プロセスの flock 再入は自分自身と競合して止まる） |
| P2-5 | `spill_instruction(what, then)` を新設 | flow / amigos / project の文言が**わずかに変わった**（枠が統一されたため） | 既存テストは文言を固定していない（実測）。変わったのは語順だけで、「必ず読み込ませる」という効き目に関わる部分は同じ |
| P2-5 | JS の symlink 解決を Python と揃える | `fs.realpathSync.native` → 失敗したら `path.resolve` の 2 段。ゴールデン表は **fs に依存しない項目だけ**にした | 一時パスは決定的でないので表に載せられない。symlink 解決そのものは両言語の個別テストで確かめる |
| P2-5 | `gitbus._safe` は触らない | flow の**板レイアウトに使う 3 箇所だけ** `protocol.safe_name` へ寄せた（クローン先ディレクトリ名は `_safe` のまま） | 置換文字を変えると既存 run のディレクトリ名が変わる＝静止点の意味が変わる。板の名義は板の規則で綴る、が今回の線引き |
| P2-5 | — | **`verify.py` / スキルの `workspace.url` 空値の描画も揃えた**（§7-F） | `diff_criterion` と同じファイルの同じ関数なので、別コミットに割ると読み手が 2 度同じ場所を読むことになる |

### 9.1 実測（実装後）

| 対象 | 結果 |
|---|---|
| agent-project | 1,174 件 緑（修正前は 1,127 件。うち 31 件は「今まで走っていなかった分」の回収） |
| agent-flow | 577 件 緑（新規 6 件） |
| agent-amigos | 180 件 緑（新規 4 件） |
| agentcore（テストルート 2 つ） | 82 / 82 件 緑（新規 18 件） |
| agent-dashboard `npm test` | 緑（新規 2 ファイル: 契約バージョンと URL 正規形のゴールデン） |
| R10 検査 | 違反なし |

既存テストで**書き換えたのは 1 件だけ**:
`test_writes_node_capability_with_local_clone_declaration` は「板に `local` が載る」ことを
固定していたので、新しい契約（url だけを配る）へ寄せて `..._with_repo_urls` へ改名し、
「`local` を落としても手元クローンの解決は効く」を別テストで足した。

### 9.2 実装中に見つけたもの（§7 に無いもの）

| # | 内容 | 重要度 | 扱い |
|---|---|---|---|
| L | **`unittest discover` が集めないテストファイルが 4 つあった**。`tests/test_resident_{scheduler,status,supervisor,worker}.py` はモジュール直下の `def test_*` で書かれており、`discover` は `TestCase` サブクラスしか集めない。**31 件が CI で緑とも赤とも報告されていなかった**（`if __name__` の手動実行でしか走らない）。P3-1 で CI を入れたとき「4 パッケージの単体テスト」と書いたが、実際にはこの 4 ファイルが素通りしていた。しかも中身は `resident` の中核（scheduler / supervisor / worker / status）で、P0-1（SIGTERM 窓）と P2-3（ワーカープール）がまさに触る場所 | 高 | **本設計の中で直した**（`tests/_functest.py` の `load_tests` フック）。P2 のテストがこの 4 ファイルへ入るので、直さないと新しい護りも走らない |
| M | **`declared_workloads` の綴り間違いは型検査を通る**。`workloads: [flwo]` は配列で文字列なので W6 に掛からず、結果は「板の仕事を 1 つも受けない」——`agent_cli` のスカラ分解（P1 §7-C）と同じ無言の不参加 | 中 | P2-3 の `host_config_findings` へ語彙の検査を追加（§9 の表） |
| N | **dashboard のテストは `yaml` の実体を要求する**。`nodeRepos.js` が `base/main/yaml.js` 経由で `yaml` を require するため、依存を入れずに `npm test` を叩くと `MODULE_NOT_FOUND` で落ちる。CI は `npm install --omit=dev` を先に走らせるので緑だが、手元の手順（README / P1 §5.6 の 6 コマンド）にはその一行が無い | 低 | 本設計では手順に触れていない。手元で回すときは `npm install --omit=dev` を先に打つ（CI と同じ）。README への追記は次に手順を触るときに |
