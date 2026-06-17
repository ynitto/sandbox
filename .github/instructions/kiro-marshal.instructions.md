---
applyTo: "**/backlog/*.md"
---

# kiro-marshal 規約 — backlog/・policy.md・needs/・decisions/

Loop Engineering の MVP。**`backlog/`（案件毎ファイル）を優先順位付けし、最優先タスクを kiro-flow に
実行させ、verify ゲートで検証し、done はファイル削除・NG なら積み直す——backlog が尽きるか予算が
尽きるまで繰り返す**制御層。人の判断が要った分は案件毎の `needs/<id>.md`（フィードバック欄つき）で
差し出し、判断は `decisions/<id>.md` に残す。実体は `tools/kiro-marshal/` と `tools/kiro-flow/`。
詳細設計は `docs/designs/2026-06-16-kiro-marshal-mvp-design.md`。

## 正準ループ（5点）

1. backlog/<id>.md を読み優先順位をつけ、最優先を kiro-flow に投げる。
2. 優先順位付けは原則 kiro-cli。`stub` なら最古優先（FIFO）。人間は `policy.md` で上書きできる。
3. kiro-flow の結果を verify ゲートで検証。done はファイル削除、NG なら積み直す。
4. drained or 予算切れ（budget=サイクル数/実時間）まで反復。`--watch` なら以後も backlog/ を監視
   （idle 中はエージェントを起動しない）。
5. ユーザーの判断・フィードバックは案件毎 `decisions/<id>.md` に保存。

## backlog/<id>.md 規約（1ファイル＝1タスク。id はファイル名）

```markdown
## <id>: <タイトル>
- status: inbox | ready | doing | done | blocked
- source: human | triage | followup
- verify: `終了コード0をPASSとみなすシェルコマンド`
- retries: 0
- note: 任意（保持される）
```

- `ready`（実行待ち）を**最古優先（mtime 昇順）**で消化。`inbox` は triage で verify があれば
  `ready` に昇格、無ければ据え置き（acceptance 未定義として人へ）。
- **done になったファイルは削除**（痕跡は journal）。`backlog/` には常に未完だけが残る。

## 鉄則（MVP の存在意義）

1. **done は自己申告では確定しない。** `verify` の終了コード 0 だけが done の根拠。
2. **verify を持たないタスクは done 不能。** 人の判断（`blocked`＋`needs/<id>.md`）へ回す。
3. **必ず有限回で止まる。** `drained` か `budget` に到達する（`--watch` 時も idle はエージェント非起動）。

## 人間が触る面

| パス | 役割 | 書く主体 |
|------|------|----------|
| `backlog/<id>.md` | タスク本体（案件毎） | 人＋システム |
| `policy.md` | 優先順位・実行先の上書き（`deny`/`pin`/`defer`/`offload`、ID/タイトル部分一致） | **人だけ** |
| `needs/<id>.md` | 判断待ちの通知＋**フィードバック記入欄** | システム生成・人が記入 |
| `decisions/<id>.md` | 人の判断・承認・フィードバックの決定記録（append-only） | システム |

precedence は厳格に **人間 policy ＞ エージェント提案**。

## 実行

```bash
kiro-marshal run --executor kiro                  # backlog/ を自律消化
kiro-marshal run --watch --poll 10 --executor kiro  # 常駐（新規/フィードバック監視）
kiro-marshal run --planner stub --executor stub   # kiro-cli 無しで確認
kiro-marshal needs                                # 人の判断待ちを表示
kiro-marshal approve <id> --reason "…"            # 承認して積み直し（→ decisions/<id>.md）
kiro-marshal hold <id> --reason "…"               # 保留（policy.deny 追加）
```

終了コード（非 watch）: `0`=完走で判断待ち無し / `1`=判断待ちあり / `2`=予算停止。CI に組める。
**フィードバック往復**: 判断待ちは `needs/<id>.md` を生成。人が「## フィードバック」欄に記入すると
次パスで拾われ、ブロック解除＋内容を次の act に反映する。

## エージェントの振る舞い

- 「回して」と言われたら `kiro-marshal run` を起動し、停止後は
  **判断待ち（blocked）と停止理由を報告**する（勝手に done 扱いしない）。
- backlog にタスクを追加するときは `backlog/<id>.md` を1ファイルで作り、**必ず実行可能な `verify` を
  付ける**。書けないなら分解が粗い兆候。
- 優先順位を機械に任せたくない時は `policy.md` に `deny`/`pin`/`defer` を書く（人間が必ず勝つ）。
- 曖昧で人間判断が要るタスクは積まずに確認する。ループは「機械的に検証できる作業」を回す箱。
