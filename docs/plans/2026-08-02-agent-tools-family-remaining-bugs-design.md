# agent-tools ファミリ未修正バグの設計

## 1. 結論

[`2026-08-02-agent-tools-family-bug-audit.md`](../reviews/2026-08-02-agent-tools-family-bug-audit.md)
のうち、仕様判断が必要な FL2 / FL3 / AM2 / D2 / D5 は次の方針で修正する。

| ID | 採用方針 |
|---|---|
| FL2 | verify の実行成功と検証不合格を分離し、継続規則を排他的にする |
| FL3 | workspace 準備失敗を `noop` にせず fail-close し、base-sync を制御層で完結させる |
| AM2 | 大小比較カーソルを廃止し、メッセージ ID の既読集合を使う |
| D2 | `reply_to` を返信元メッセージ ID に統一する |
| D5 | controller は既定で tmux 内、oneshot は detached とし、画面を自動切替しない |

柱 1 / C2・C7 — 分散配送と委譲の判定を決定的にし、失敗を偽の終端へ倒さない。
柱 2 / C3・C6 — 自動化が人の画面を奪わず、未回答を配送順や時計ずれで失わない。

## 2. 不変条件

1. 内容の不合格と、その判定処理自体の実行失敗を混ぜない。
2. `noop` は「実行したうえで作業不要」のときだけ使う。
3. ワークスペースの commit / merge / push / rebase は agent-flow 制御層だけが行う。
4. 分散配送される ID の順序とファイルの到着順は一致すると仮定しない。
5. 1 フィールドに 2 つの意味を持たせない。
6. 定期実行デーモンは利用者の tmux client を自動で切り替えない。

## 3. FL2: verify の二重継続

### 3.1 状態契約

verify executor が正常に応答し、判定が不合格だった場合は次の形にする。

```json
{"status": "done", "data": {"ok": false}}
```

`status=done` は検証処理の完了、`data.ok=false` は検証対象の不合格を表す。
CLI 停止、タイムアウト、壊れた出力など verify 処理自体が完了しない場合だけ
`status=failed` とする。

### 3.2 継続規則

1 ノードに対して次のどちらか 1 つだけを適用する。

1. `kind=verify` かつ `data.ok=false`: 上流の成果ノードと対応する verify を作り直す。
2. それ以外で `status=failed`: 失敗したノード自体を再試行する。

実装上も `if ... elif ...` で排他性を表現し、正規化処理の将来変更で二重発火しないようにする。

### 3.3 検証

- verify 不合格 1 回で「上流 retry + verify retry」のみ生成する。
- deps が空の孤児 verify を生成しない。
- verify executor の実行エラーは verify 自体の retry になる。

## 4. FL3: base-sync の fail-close

2026-08-01 の
[`agent-flow base 同期と統合 receipt 設計`](./2026-08-01-agent-flow-base-sync-and-integration-receipt-design.md)
で決めた system node と target revision 固定は維持する。

### 4.1 終端化条件

| 状況 | 結果 |
|---|---|
| workspace または target が契約上不要 | `noop` |
| clone 成功、target revision は既に HEAD の祖先 | `noop` |
| clone / fetch / revision 解決失敗 | `failed` + 既存 `error_class` |
| merge・検査・commit・push すべて成功 | `done` |

workspace spec が存在するのに clone を用意できない場合、`ensure_workspace_clone()` は
空文字を返さず、最後の git stderr を保持した例外にする。既存の失敗分類器で
`transient` / `auth` / `config` 等に分類し、既存の auto-heal へ渡す。

### 4.2 executor 境界

- clean merge と Git 検査は常に agent-flow 制御層が行う。
- content conflict の編集が必要な場合は、run の executor に関係なくローカル agent executor を使う。
- ローカル agent CLI を使えなければ `error_class=integration` で止め、ローカル worktree を
  GitLab イシューへ委譲しない。
- base-sync は park / deferred wait を作らない。したがって clone 破棄後に wait から `done` を書く
  経路も持たない。

### 4.3 push 競合

push reject 後の fetch / rebase は、作業ブランチ上の同時 push を統合するためだけ残す。
rebase 後、再 push の直前に target revision が HEAD の祖先であることを再検査する。
検査失敗時は push せず base-sync を `failed` にする。

### 4.4 検証

- provisioning 失敗で executor / finalize を呼ばず `failed` になる。
- fetch 失敗は `transient`、認証失敗は `auth` に分類される。
- GitLab executor でも base-sync をローカル GitLab イシューにしない。
- push retry 後に target 祖先性を失った成果を push しない。

## 5. AM2: メッセージ既読集合

### 5.1 契約

status に新しい `seen_message_ids` を追加する。

```json
{"seen_message_ids": ["id-a", "id-b"]}
```

`new_messages()` は inbox と all チャンネルを ID で重複除去し、既読集合に存在しない
メッセージだけを新着として返す。ID ソートは LLM へ渡す表示順だけに使い、
新着判定には使わない。

ミッションはターンと予算で有限なため、既読集合は最初は圧縮しない。
status サイズが実測上の問題になったときだけ、送信者別 ack へ再設計する。

### 5.2 旧 cursor からの移行

`seen_message_ids` が無く旧 `cursor` がある status は、現在見える `id <= cursor` を既読として
初期化する。ただし、現在の `open_questions` を `reply_to` で参照する answer は既読に入れず、
再観測して質問を閉じる。移行後に遅延到着した古い ID も集合に無いため新着になる。

これにより、旧メッセージの一括再実行を避けつつ、監査で問題になった未回答固定は回復する。

### 5.3 検証

- 新しい ID を先に読んだ後で、遅延到着した古い ID を 1 回だけ読む。
- 時計が戻った別プロセスの ID も読む。
- inbox と all に同じ ID があっても 1 件として扱う。
- 移行時に open question への既存 answer を取りこぼさない。

## 6. D2: `reply_to` の統一

`reply_to` は返信元メッセージ ID だけを保持する。返信先エージェントは受信メッセージの
`from` から決める。

```json
{
  "from": "worker1",
  "to": "orchestrator",
  "reply_to": "9f8e7d6c5b4a3928"
}
```

- `--reply-to` 未指定時は `null` またはキー省略とし、エージェント名にフォールバックしない。
- agent-loop の現行設計を正典とし、kiro-loop の送信実装と設計書を合わせる。
- 旧 kiro-loop が書いたエージェント名形式は読み込み自体を拒否せず、会話参照としてだけ無視する。
- 新旧どちらの受信実装も返信先を `from` から組み立てるため、ローリング更新中も配送は維持される。

検証は agent-loop 生成メッセージ、kiro-loop 生成メッセージ、旧形式メッセージの 3 種を
同じ共有 inbox に置き、返信先とスレッド参照を突き合わせる。

## 7. D5: tmux 実行モデル

### 7.1 controller

- 通常起動は `_auto_attach_tmux_if_needed` により controller を専用 tmux セッション内へ再実行する。
- `--no-auto-attach` 指定または tmux 不在時は、controller を現在のプロセスで headless 実行する。
- 設計書上の「常に tmux 内」は「既定は tmux 内」に改める。

### 7.2 oneshot

- oneshot worker は controller と別の detached tmux セッションで動かす。
- controller や scheduler から `attach-session` / `switch-client` を呼ばない。
- 作成したセッション名をログと状態に記録し、利用者が必要なときだけ明示的に接続する。
- oneshot 完了時は client の有無に関係なく worker session を破棄する。

oneshot 自体は現時点で未実装である。このバグ修正では矛盾した設計書の確定だけを行い、
oneshot のスキャフォールディングは行わない。

## 8. 比較した案

| 案 | 実装コスト | リスク | 保守性 | 拡張性 | 学習コスト | 推奨度 |
|---|---|---|---|---|---|---|
| A. 局所的に契約を正す（採用） | 低〜中 | 低 | 高 | 中 | 低 | ★★★ |
| B. 現状の意味を残し個別ガードを足す | 低 | 中 | 低 | 低 | 中 | ★☆☆ |
| C. 新しい配信台帳・統合サービスを作る | 高 | 高 | 中 | 高 | 高 | ★☆☆ |

案 B は同じ意味の判定が複数経路に残るため C7 に反する。案 C は中央調整役を増やし C2 と
YAGNI に反する。

## 9. 実装計画

### Phase 1: 偽終端と重複実行を止める

1. FL2 の verify status 正規化と継続規則の排他化。
2. FL3 の clone / fetch / ancestry 失敗を fail-close。
3. それぞれ 1 ケースの最小回帰テストを先に追加する。

### Phase 2: 分散メッセージを安定化する

1. AM2 の既読集合と旧 cursor 移行。
2. D2 の kiro-loop 送信契約と設計書を更新。
3. 遅延 push、時計ずれ、新旧 sender 混在を回帰テストにする。

### Phase 3: 文書の矛盾を閉じる

1. D5 の oneshot 設計書を確定した tmux モデルへ修正する。
2. 監査文書の対応状況を更新する。
3. ツール単位のテストを実行し、一括 `pytest tools/` は使わない。

## Decision Record

| 項目 | 内容 |
|---|---|
| 決定日 | 2026-08-02 |
| 決定者 | nitto |
| 採用案 | 局所契約修正: verify 意味分離、base-sync fail-close、既読 ID 集合、`reply_to` ID 統一、headless oneshot |
| 却下案 | 個別ガードの積み増し（判定重複）、新しい中央台帳（C2 違反・過剰） |
| 主な理由 | 既存の所有境界と失敗分類を保ちながら、偽終端・二重発火・配送欠落の原因をそれぞれ 1 か所で除くため |
| トレードオフ | AM2 の status にミッション中の既読 ID が蓄積する。oneshot は自動で画面に出ない |
| 再評価条件 | 既読集合が status サイズの実測問題になる、または oneshot の明示的な画面接続要求が確定するとき |
