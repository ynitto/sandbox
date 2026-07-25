# node_id 切替手順（静止点）

> 参照計画: [`docs/plans/2026-07-24-single-resident-controller-implementation-plan.md`](../plans/2026-07-24-single-resident-controller-implementation-plan.md) W1-10。
> 参照設計: [`docs/plans/2026-07-24-single-resident-controller-design.md`](../plans/2026-07-24-single-resident-controller-design.md) §3.1・§9 C13。
> **本手順は静止点（実行中の委譲・ミッションが無い状態）でのみ行う。** 稼働中の node_id を
> 無停止で書き換える経路は無い（設計 §1.3「node_id 統一と語彙統一は静止点イベント」）。

## 何を変えるのか

node_id は板（agent-board）とプロトコル上の「PC の身元」。設計は node_id = PC 名を既定とする
（§3.1 用語集）。実装計画 W1-10 で各エンジンの既定採番を PC 名（ホスト名）へ揃えた:

- **agent-flow**: daemon の `node_id` は従来 `hostname-pid` で daemon 再起動ごとに変わっていた。
  今後は `--node-id` 未指定なら常に安定したホスト名になる。
- **agent-amigos**: `node.json` が無い新規ノードの採番は従来 `hostname-乱数4桁` だった。
  今後は新規採番も安定したホスト名になる。

綴りの正規化は `agentcore.nodeid.normalize_node_id`（小文字・`[a-z0-9._-]` のみ）に一本化して
いる。エンジンごとに正規化を持つと同じ PC が agent-flow で `Mac`・agent-amigos で `mac` になり、
板に 2 ノードとして現れる（大小文字を区別しないファイルシステムでは互いを上書きする）。
`--node-id` / `AGENT_AMIGOS_NODE` / `node.json` で**明示指定した値はそのまま使う**ので、
手で指定するときは自分で正規形（小文字・記号は `.`/`_`/`-` のみ）に揃える。

**既存ノードは自動移行しない。** 既に `node.json`（amigos）や `--node-id` 明示指定（flow）で
稼働しているノードの名義は、今回のコード変更だけでは変わらない——同一性の断絶（claim / assign /
板の bid の宛先が変わる）を避けるため、切替は下記手順で人が明示的に行う。

## 前提

- 対象ノードが担当している**委譲・ミッションが実行中でない**こと（下記の doctor チェックで確認）。
- 板（agent-board）と amigos バスへの書き込み権限があること。
- 対象ノードの `agent-project` / `agent-flow` / `agent-amigos` を停止できること。

## 手順

### 1. 事前チェック（doctor）

旧名義（例 `pc-a-3f2c` のような乱数接尾辞付き ID）に未決着の委譲・ミッションが残っていないか
確認する:

```python
from agent_project.doctor import doctor_node_id_cutover_findings

findings = doctor_node_id_cutover_findings(
    board_root="/path/to/board",       # 板のローカルクローン（無ければ None）
    old_node_id="pc-a-3f2c",
    new_node_id="pc-a",
    amigos_bus_root="/path/to/amigos-bus",  # amigos バスのローカルクローン（無ければ省略可）
)
for f in findings:
    print(f["title"], "-", f["evidence"])
```

- **「旧 node_id 名義の委譲が実行中」** が出たら、対象の委譲が終端（`result.json` 生成）するまで
  待つ。板の該当 `delegations/<id>/status/<old>.json` を見て、駆動しているノードの run/ミッションを
  確認してもよい。
- **「旧 node_id 名義の amigos ロール状態が残存」** が出たら、そのミッションが実際に終端しているか
  人が確認する（doctor は「見つかったら人が見る」に倒す設計——ミッション文脈まで doctor は判定
  しない）。away（計画停止）中の役なら resume を待つか、オーナーに手放しを依頼する。
- **「新 node_id が板で使用中」** が出たら、切り替え先の名義を別 PC が生存状態で使っている。
  既定採番が PC 名になったことでホスト名の重複（`localhost`・コンテナ既定名）が現実に起こる。
  そのまま切り替えると 2 台が同じ名義で入札し bid/status を互いに上書きするので、
  一意な node_id を明示指定する。

両方とも所見が空になるまで、切替に進まない。

### 2. 対象ノードを止める

```bash
# 対象 PC で常駐体を止める（away 宣言が入る）
systemctl --user stop agent-project.service   # systemd 構成の場合
# フォアグラウンド起動なら Ctrl-C
```

停止すると、板・amigos バスへの新規入札は止まる。既存の bid/status は旧名義のまま残るが、
lease が切れれば他ノードから孤児回収される（設計 §6 の回復表どおり・人の介入不要）。

### 3. 新しい node_id を明示する

- **agent-flow**: `--node-id pc-a`（または CLI ラッパの設定ファイルに固定値を書く）。
  省略時は次回起動でホスト名が自動採用されるので、ホスト名をそのまま使うなら省略でよい。
- **agent-amigos**: `~/.agents/amigos/node.json` を編集するか削除する。
  - 編集: `{"id": "pc-a"}` に書き換える。
  - 削除: 次回起動時にホスト名で自動再採番される（`AGENT_AMIGOS_NODE` 環境変数が優先されるので
    未設定であることを確認する）。

### 4. 旧名義のクローンパスを整理する（任意・容量整理のみ）

node_id はローカルのクローンパスにも使われる（例:
`~/.agents/flow-board/<board-sha1>/<old_node_id>/`、amigos の `BoardMirror` 作業領域）。
新 node_id で起動すると**新しいパスに再クローンされる**だけで、旧パスは自動では消えない。
残しておいても実害は無い（単なる孤立ディレクトリ）が、容量が気になる場合だけ手動で削除する:

```bash
rm -rf ~/.agents/flow-board/*/<old_node_id>
# amigos の board_workdir 配下も同様
```

### 5. 起動して確認する

新 node_id で起動し、板 `nodes/<new_node_id>.json`（W1-6 で実装したノード契約）が
書かれること・`agent-project doctor` が新名義でエラーを出さないことを確認する。

## トラブルシュート

| 症状 | 原因 | 対処 |
|---|---|---|
| 切替後、旧名義の委譲が誰にも拾われない | 旧名義の bid の lease が有効なまま | lease 失効を待つ（設計 §6 は自動回収を保証）。急ぐなら板の該当 `bids/<old>.json` を手動削除 |
| amigos の役が「away のまま復帰しない」 | resume_at 前に旧名義が消えた | オーナーが `restaff`（未充足ロールの再募集）を実行する |
| doctor が誤って「実行中」と報告し続ける | `result.json` が生成される前に板の同期が止まっている | 板リポジトリの pull/push が動いているか確認（`agent-project doctor` の同期系所見を見る） |
