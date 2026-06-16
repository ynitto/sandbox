---
applyTo: "**/queue.md"
---

# task-loop 規約 — queue.md と停止条件

Loop Engineering の MVP。**人間がプロンプトを毎サイクル投げ込まなくても、
`queue.md` のタスクが枯れるか停止条件に達するまで自律的に回り続ける**最小の閉ループ。
実体は `tools/task-loop/`（ランナー）と `tools/kiro-flow/`（実行委譲先）。

## 二層構成

| 層 | 担当 | 実体 |
|----|------|------|
| 外側ループ | queue.md の状態管理 / 停止条件 / **真の verify ゲート** | `task-loop` |
| 内側実行 | タスクの分解 → act → 内側 verify ループ | `kiro-flow run` |

各サイクルは **「todo を 1 件 claim → act → verify → 状態更新 → 申し送り」** を回す。

## queue.md 規約

```markdown
## <id>: <タイトル>
- status: todo | doing | done | blocked
- verify: `終了コード0をPASSとみなすシェルコマンド`
- retries: 0
- note: 任意の自由記述（保持される）
```

- タスクは `## <id>: <title>` 見出しで始め、直後の `- key: value` 行をメタdata とする。
- `todo` を**上から順**に消化する。`done`/`blocked` は飛ばす。
- `verify`・`retries` 以外の `- key: value`（`note` 等）は順序を保って保持・書き戻される。

## 鉄則（この 3 つが MVP の存在意義）

1. **done は自己申告では確定しない。** `verify` コマンドの終了コード 0 だけが done の根拠。
   エージェントが「できました」と言っても、verify が通らなければ done にしない。
2. **verify を持たないタスクは done 不能。** verify 未定義のタスクは即 `blocked` にして人間へ回す。
3. **ループは必ず有限回で止まる。** 下記いずれかの停止条件に必ず到達する。

## 停止条件（いずれかで停止しエスカレーション）

| 理由 | 既定 | 意味 |
|------|------|------|
| `drained` | — | `todo` が尽きた（実質完了） |
| `max_cycles` | 20 | 外側ループのサイクル上限 |
| `no_progress` | 3 | `done` 件数が N サイクル増えていない（停滞） |
| `blocked_ratio` | 0.5 | `blocked` 比率がこれ以上 |
| `budget` | 無制限 | 実時間予算（`--max-seconds`）超過 |

タスク単位では `retries > max_retries`（既定 2）で `todo → blocked` に落とす。

## 実行

```bash
# キューを自律消化（act は kiro-flow に委譲）
task-loop --queue queue.md --executor kiro

# kiro-cli 無しでプロトコル確認（stub）
task-loop --queue queue.md --executor stub --planner stub

# act を飛ばし verify だけで状態を整合（既存成果の点検・再開前の棚卸し）
task-loop --queue queue.md --dry-run
```

終了コード: `0`=完走で blocked 無し / `1`=blocked あり / `2`=ガードで停止。CI に組める。
申し送りは `journal.md` に追記される（次サイクル・次セッションが読む短期メモリ）。

## エージェントの振る舞い

- このループを「回して」と言われたら `task-loop` を起動し、停止後は
  **`blocked`/`todo` の残タスクと停止理由を報告**する（勝手に done 扱いしない）。
- タスクを queue.md に追加するときは**必ず実行可能な `verify` を付ける**。付けられないタスクは
  分解が粗い兆候——`verify` を書けるところまで分解してから積む。
- 曖昧で人間判断が要るタスクは積まずに確認する。ループは「機械的に検証できる作業」を回す箱。
