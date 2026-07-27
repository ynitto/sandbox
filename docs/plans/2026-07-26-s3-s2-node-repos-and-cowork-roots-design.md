# S3 + S2 詳細設計: ノード固有ローカルリポジトリ層 / 定常業務フォルダの dashboard 管理

ステータス: 実装済み(詳細設計 + 実装で確定した差分を反映)
入力: [`2026-07-25-agent-improvement-spec.md`](2026-07-25-agent-improvement-spec.md) §3 S3(C3・C4) / S2(C2)
前提: [`2026-07-26-s1-config-two-layer-detailed-design.md`](2026-07-26-s1-config-two-layer-detailed-design.md)(実装済み。host.yaml `repos[]` と `resolve_local_repo` の入口が既にある)
実装フェーズ: Phase 1 の残り

S3 と S2 は独立だが、どちらも「宣言は実行側が持つ」という同じ原則の適用なので 1 本にまとめる。

---

## 1. S3: ノード固有ローカルリポジトリ層(C3・C4)

### 1.1 現状(調査結果)

clone 効率化の機構は実装済みで、欠けているのは**宣言場所**と**適用範囲**だけ。

| 要素 | 状態 |
|---|---|
| 共有 bare ミラー + detached worktree | 実装済み(`agent_flow/gitcache.py`) |
| `local` からの worktree 切り出し(`provision_from_local`) | 実装済み(`gitcache.py:170-212`) |
| `local` の伝搬 repos.json → agent-project → agent-flow | 実装済み(`charter.py:314` → `request.py:490` → `workspace.py:157-158`) |
| host.yaml `repos[]` の読み取り | S1 で実装済み(`HostConfig.repos` / `_normalize_host_repos`) |
| host.yaml `repos[]` からの解決 | S1 で agent-project の検収 diff 経路のみ(`config.py:resolve_local_repo`) |
| 板の請負側で自ノード `local` をマージ | **未実装**(`agent_flow/board.py:273-278` は公示 workspace をそのまま submit) |
| dashboard の CLI チャット cwd 選択 | **未実装**(`agent.js:249-258` は選択中プロジェクト 1 択) |

**唯一の矛盾**: `local` / `dir` はホスト固有の絶対パスなのに、宣言できる場所が共有 repos.json しかない。repos.json は charter から自動生成され(`charter.py:365-397`)、状態リポジトリ経由で全 PC へ push される(`stategit.py` の `_STATE_REMOTE_WINS_FILES`)。つまり **1 台で書いた `/home/me/mirrors/app` が全 PC に配られる**。

### 1.2 仕様

#### (1) 共通リゾルバを agentcore に置く

URL 正規化一致の実装が 3 か所に分かれている:

- `agent_project/state.py:_same_git_remote`(末尾 `.git`・スラッシュ・ローカルパス絶対化を吸収)
- `agent_flow/gitcache.py:_same_repo`(末尾 `.git`・スラッシュ・**小文字化**)
- `agent_flow/board.py:_norm_repo_url`(同上・入札選別用)

吸収規則が微妙に違う(絶対化する/しない、小文字化する/しない)ため、**同じ 2 つの URL が経路によって一致したりしなかったりする**。agentcore に 1 実装を置き、3 者がそれを使う(agentcore は既に `nodeid` で同じ問題を解決した前例がある — 各エンジンが独自正規化して板で別ノードに見えた件)。

`agentcore/repolocal.py`:

```python
normalize_repo_url(url) -> str        # 末尾 .git / スラッシュ / 小文字化 / ローカルパス絶対化
same_repo(a, b) -> bool               # 上の正規形で比較
load_node_repos(path=None) -> list    # host.yaml の repos[] を [{url, local}, …] で返す
resolve_local(url, repos=None) -> str # URL → このノードのローカルクローン(無ければ "")
merge_local(spec, repos=None) -> dict # workspace spec に local を埋める(既にあれば尊重)
```

正規化の統一で**入札判定が変わりうる**点は意識して扱う: `board_eligible` は現在 `_norm_repo_url`(小文字化あり・絶対化なし)で照合している。統一後も大小文字を無視する点は同じで、変わるのは「ローカルパス表記の URL を絶対化して比較するようになる」ことだけ。板の公示 URL は通常 git URL なので実挙動は変わらないが、テストで固定する。

#### (2) `local` / `dir` を repos.json から撤去

- `schemas/repos.schema.json`: `local` / `dir` を **deprecated** と明記し、置き場を host.yaml `repos[]` と示す(スキーマからは消さない — 既存ファイルを不正にしない)
- `charter.py:_registry_entry`: `local` を読んだら**警告 1 回**して無視する(値は spec に載せない)。`dir` は元々 `path` の別名として吸収されている(`charter.py:108`)ので、そちらは触らない
- `export_repo_registry`: 元から `local` を書き出していない(`charter.py:378-380` は url/desc/base/target/path/owns/docs/tests/code のみ)ので変更不要

**なぜ削除ではなく警告か**: repos.json は人が手書きもできる(`_meta` を消せば手書きが正)。既に `local` を書いて動かしているノードがあるとき、黙って無視すると「速くなっていたはずの経路が静かに遅くなる」。警告で移行先を示す。

#### (3) 全経路で使う

| 経路 | 現状 | 変更 |
|---|---|---|
| agent-project → agent-flow の `--workspace`(`request.py:_workspace_token`) | spec の `local`(repos.json 由来)を伝搬 | 送出直前に `merge_local` でノード宣言から埋める |
| agent-project の検収 diff(`config.py:_source_repo`) | S1 で host.yaml から解決済み | agentcore へ委譲(重複実装を解消) |
| agent-flow の provision(`workspace.py:157-158`) | spec の `local` のみ | spec に無ければ自ノード宣言から解決 |
| **板の請負側**(`agent_flow/board.py:poll_board`) | 公示 workspace をそのまま submit | submit 前に `merge_local` を通す(**欠落の修正**) |
| dashboard の CLI 起動 | — | (4) |

板の請負側が要点。公示 workspace に依頼側の `local` が載らないのは正しい(依頼側のパスは請負ノードに存在しない)が、請負側が**自分の** `local` を載せる実装が無いため、板経由の仕事は常にネットワーク越しのミラー取得になる。これは C3(「flow/amigos の git clone のリモート負荷」)そのもの。

#### (4) dashboard の CLI チャット cwd 選択

起動ボタンに cwd 候補のドロップダウンを追加する。

- 候補 ①選択中プロジェクトのフォルダ(既定・従来動作)
- 候補 ② repos.json の各リポジトリのうち、ノード宣言で `local` を解決できたパス
- 解決できないリポジトリは**非活性**で表示(「なぜ選べないか」が分かる方が、一覧から消えるより良い)
- パス手入力(その場限り)も許す

dashboard は host.yaml を**読むだけ**(編集しない)。読み取りは JS 側の実装が要る(Python を起動しない — UI の応答性のため)。

#### (5) 板の `nodes/<node-id>.json` への転記 — 本設計ではスコープ外

仕様書 S3-5 は「板の `nodes/<node-id>.json` の `repos[].local` は host.yaml から転記する」だが、**`nodes/<node-id>.json` を書く実装自体がまだ無い**(`resident_cli.py` 冒頭に「nodes/<pc>.json のノード能力宣言はまだ無い」と明記。実装計画 W1-11 残)。書き手が無いものへ転記はできないので、S8/W1-11 と同時に行う。

> **訂正（2026-07-27）**: 本節の「書く実装がまだ無い」は R2a（S8 §6.5）で解消済み。常駐体の
> board tick（`resident_cli.py` の `_board_participate_tick`）が `nodes/<node-id>.json` を書き、
> host.yaml の `repos[]` も `_board_repo_declaration` で転記される（P2-2）。本節のスコープ外
> 判断は当時のままで正しく、待ち先だった実装が入った、という訂正。

代わりに、いま動いている入札選別(`agent-flow` の `board_repos` 設定)は (1) の共通リゾルバで正規化を揃える。

### 1.3 非目標

リモートへの fetch 回数削減。鮮度不変条件 INV-1(毎 fetch → fetch 後 SHA)は維持する(`git-worktree-cache-pattern.md` の非目標を踏襲)。`local` は「どこから取るか」を変えるだけで、「取るかどうか」は変えない。

---

## 2. S2: 定常業務フォルダの dashboard 管理(C2)

### 2.1 現状(調査結果)

- 定常業務(cowork)の走査ルートは `engine.projectRoots(config)` = `engine/status.json` の `children[].viewerRoot` のみ(`cowork/main/discover.js:270-284`)。設定 `roots` は W2-4 で廃止済み
- つまり **agent-project 管理外のフォルダ**(kiro-loop 設定や `.statemachine/` を持つだけ)を定常業務画面に出す経路が無い
- 表示側の非プロジェクト分岐は既にある(`renderer.js:1605-1618` `workspaceFeatureModel`。`isProject=false` なら cowork タブのみ・既定タブも cowork)
- マーカー検出(`detectMarkers`)・ステートマシン新規作成動線(`stateMachineCreationPrompt`)も実装済み

### 2.2 なぜ host.yaml ではなく dashboard か

定常業務のエンジン(kiro-loop / statemachine-use)は agent-project の常駐体・状態リポジトリ・バックログと**無関係に動作する**。起動・tmux セッション管理・履歴記録はすべて dashboard の cowork feature が担う。

W2-4 で廃止したのは「**agent-project プロジェクト一覧**の二重管理」であり、その原則は「宣言は実行側が持つ」。定常業務の実行側は dashboard 自身なので、宣言も dashboard 設定に置くのが原則に合致する。host.yaml に載せると、常駐体が管理しないものを常駐体の宣言ファイルに書くねじれが生じる。

### 2.3 仕様

1. **`cowork.roots[]` を dashboard 設定に追加**(`features/cowork/config.js`)。定常業務ワークスペースのフォルダパス一覧。
2. **走査ルートの和集合**: `discoverCoworkItems` の roots を `engine children + cowork.roots` にする。重複は既存の `seenRoots`(`_pathKey` 正規化)が畳む。
3. **プロジェクトセレクタへの合流**: `cowork.roots` のエントリを **kind=routine** として一覧に足し、既存の `isProject=false` 分岐(cowork タブのみ・既定タブ cowork)へ流す。
4. **登録 UI を定常業務タブに置く**: フォルダ選択 → マーカー検出(`detectMarkers` 流用)→ プレビュー → 登録。マーカーが無いフォルダは「ステートマシン新規作成」動線へ誘導。登録解除も同 UI。
5. **agent-project プロジェクト一覧は engine/status.json のみ**(W2-4 維持)。`cowork.roots` に project root と同じパスが登録されたら **project 側を正**として畳む。
6. **host.yaml は変更しない**(S1 から routines 案を撤回済み)。

### 2.4 重複の畳み方(5 の詳細)

同じフォルダが両方に現れたとき、project 側を残す理由: project エントリは backlog / charter / needs / 検収を持ち、routine エントリは cowork タブしか持たない。routine を残すと機能が消える。

畳む判定は `_pathKey`(既存のパス正規化。WSL UNC・大小文字を吸収)で行う。

---

## 3. 実装単位

| # | 対象 | 内容 |
|---|---|---|
| S3-a | `agentcore/repolocal.py`(新規)+ tests | 正規化・解決・マージの 1 実装 |
| S3-b | agent-project | `_same_git_remote` / `resolve_local_repo` を agentcore へ委譲。`_registry_entry` の `local` を警告して無視。`_workspace_token` で `merge_local` |
| S3-c | agent-flow | `_same_repo` / `_norm_repo_url` を agentcore へ委譲。`provision_workspace` で spec に無ければ解決。`poll_board` で submit 前に `merge_local`(欠落修正) |
| S3-d | schemas | `local` / `dir` を deprecated と明記 |
| S3-e | dashboard | CLI チャット cwd ドロップダウン(host.yaml `repos[]` の JS 読み取り) |
| S2-a | dashboard | `cowork.roots[]` 設定 + 走査ルートの和集合 + セレクタ合流(kind=routine) |
| S2-b | dashboard | 登録 UI(定常業務タブ) |
| — | ドキュメント | 両ツール README / host.yaml.example / CHANGELOG |

## 4. テスト計画

S3:
1. `normalize_repo_url` / `same_repo`: 末尾 `.git`・スラッシュ・大小文字・ローカルパス表記の吸収
2. `resolve_local`: host.yaml `repos[]` からのヒット / URL 違いのミス / `local` が実在しないときのミス
3. `merge_local`: spec に既に `local` があれば尊重 / 無ければ埋める / 該当なしなら素通り
4. repos.json の `local` は警告して無視される(spec に載らない)
5. `_workspace_token` がノード宣言の `local` を載せる
6. agent-flow の provision が spec に `local` が無くてもノード宣言から解決する
7. **`poll_board` が submit する workspace に自ノードの `local` が載る**(欠落の修正・回帰防止)
8. 板の入札選別が正規化統一後も同じ判定になる

S2:
9. `cowork.roots` が走査ルートに合流する
10. project root と同じパスの routine は畳まれる(project 側が残る)
11. routine エントリは `isProject=false` で出る(cowork タブのみ)
12. 登録・解除 IPC の往復

## 5. 実装で確定した差分

| 項目 | 実装 |
|---|---|
| S3 の `local` 撤去 | repos.json の `local` は**警告して無視**(`charter.py:_warn_registry_local`。レジストリはルーティングのたびに読まれるので警告は 1 回だけ)。`dir` は元から `path` の別名として吸収されており、`local` と違って伝搬経路が無いのでコード変更は不要——スキーマの deprecated 記載だけにした |
| S3-4 の候補一覧 | プロジェクト + host.yaml 宣言 + **プロジェクトの repos.json にあるがこのノードに宣言が無いもの**(非活性・理由付き)。3 つ目は設計に無かったが、無いと「宣言し忘れ」が候補欄から見えない |
| CLI チャットの tmux セッション名 | `<cli>:<cwd>` にした(従来は `<cli>`)。起動先を選べるようにした以上、同名で再 attach すると**別リポジトリの作業中セッションに合流する** |
| S2 のマーカー無しフォルダ | 設計は「ステートマシン新規作成へ誘導」だったが、**登録は許して案内を出す**形にした。既存の作成ダイアログは「選択中プロジェクト」を対象に動くので、登録しないと選択できず最初の 1 件も作れない(誘導先が行き止まりになる) |
| dashboard の host.yaml 読み取り | JS 側に `nodeRepos.js` を置いた(Python を起動すると候補一覧の描画がプロセス起動待ちになる)。YAML パーサを持たないアプリなので `repos:` ブロックだけを読む小さなパーサ——読めなければ「宣言なし」に倒れ、ミラー取得へ落ちるだけで動作は正しい。**訂正（2026-07-27）**: その後 dashboard は本物の YAML パーサ（`base/main/yaml.js`）を実行時依存に持ち、host.yaml の読み取りもそちらへ移設済み——「YAML パーサを持たない」前提は消えている |
| テスト環境 | agent-flow の `tests/_shared.py` にも host.yaml の隔離漏れがあった(S1 で agent-project 側は修正済みの同種の穴)。塞がないと開発者のローカルクローン宣言が provision 経路に効き、「ミラーから取るはず」のテストが手元のクローンから取って通る |

**実績**: agentcore 27 件 / agent-flow 564 件 / agent-project 951 件 / agent-dashboard 全スイート green。

## 6. 未決事項・積み残し

### 意図的に見送ったもの

1. **S3-5: `nodes/<node-id>.json` への `repos[].local` 転記** — その JSON を**書く実装自体がまだ無い**(`resident_cli.py` 冒頭に明記・実装計画 W1-11 残)。書き手が無いものへは転記できないので、S8 と同時に行う(§1.2(5))。
2. **S3: `local` の鮮度責務** — worker が毎回 `fetch` する現行方式を維持(INV-1)。ノード側の定期 fetch は「取るかどうか」を変える話なので、非目標(§1.3)に触れる。必要が出たら別途。

### 設計に書いたが実装しなかったもの(未実装として残す)

3. **S3-4 のパス手入力** — §1.2(4) は「パス手入力(その場限り)も許す」としたが、UI は**ドロップダウンのみ**を実装した。main 側(`openInteractiveChat`)は任意パスを受けて実在検査までするので、**入口(テキスト入力)を足すだけで有効になる**。宣言のあるリポジトリで用が足りるうちは候補を絞った方が誤操作が少ないと判断した。
4. **dashboard の repos.yaml/yml 読み取り** — CLIチャット候補の「プロジェクトのレジストリにあるがこのノードに宣言が無い」行は `repos.json` からのみ作る。このアプリは YAML パーサを持たないため(`nodeRepos.js` の `repos:` ブロック専用パーサと同じ制約)。**候補が減るだけで害は無い**(宣言済みリポジトリは host.yaml 側から出るので選べる)。レジストリが yaml のプロジェクトでだけ「宣言し忘れ」の可視化が効かない。

### 運用で決める余地が残るもの

5. **S2: `cowork.roots` の掃除の口** — 登録したフォルダが後から agent-project プロジェクトになった場合、表示は「毎回 project 側を正として畳む」ので自動的に正しくなるが、`cowork.roots` 側のエントリは残り続ける(害は無いが解除の動線が無い——登録解除ボタンは routine として表示されているときにしか出ない)。
