---
applyTo: "**/backlog.md"
---

# kiro-marshal 規約 — backlog.md・policy.md・DECISIONS.md

Loop Engineering の MVP。**`backlog.md` を優先順位付けし、最優先タスクを kiro-flow に実行させ、
verify ゲートで検証し、NG なら積み直す——backlog が尽きるか予算が尽きるまで繰り返す**制御層。
人の判断が要った時はそれを `DECISIONS.md` に残す。実体は `tools/kiro-marshal/` と
`tools/kiro-flow/`（実行委譲先）。詳細設計は `docs/designs/2026-06-16-kiro-marshal-mvp-design.md`。

## 正準ループ（5点）

1. backlog を読み優先順位をつけ、最優先を kiro-flow に投げる。
2. 優先順位付けは原則 kiro-cli。`stub` なら最古優先（FIFO）。人間は `policy.md` で上書きできる。
3. kiro-flow の結果を verify ゲートで検証。NG なら backlog に積み直す。
4. backlog 枯渇（drained）or 予算切れ（budget=サイクル数/実時間）まで反復。
5. ユーザーの判断は `DECISIONS.md` に保存。

## backlog.md 規約

```markdown
## <id>: <タイトル>
- status: inbox | ready | doing | done | blocked
- source: human | triage | followup
- verify: `終了コード0をPASSとみなすシェルコマンド`
- retries: 0
- note: 任意（保持される）
```

- `ready`（実行待ち）を上から順に消化。`done`/`blocked` は飛ばす。`inbox` は triage で
  verify があれば `ready` に昇格、無ければ据え置き（acceptance 未定義として人へ）。
- `status`/`source`/`verify`/`retries` 以外（`note` 等）は順序保持で書き戻す。

## 鉄則（MVP の存在意義）

1. **done は自己申告では確定しない。** `verify` の終了コード 0 だけが done の根拠。
2. **verify を持たないタスクは done 不能。** 人の判断（`blocked`）へ回す。
3. **必ず有限回で止まる。** `drained` か `budget`（サイクル数/実時間）に到達する。

## 人間が触る3面

| ファイル | 役割 | 書く主体 |
|----------|------|----------|
| `backlog.md` | タスク本体 | 人＋システム |
| `policy.md` | 優先順位・実行先の上書き（`deny`/`pin`/`defer`/`offload`、ID/タイトル部分一致） | **人だけ** |
| `DECISIONS.md` | 人の判断・承認の決定記録（append-only） | システム（人の操作から生成） |

precedence は厳格に **人間 policy ＞ エージェント提案**。

## 実行

```bash
kiro-marshal run --backlog backlog.md --executor kiro       # 自律消化
kiro-marshal run --backlog backlog.md --planner stub --executor stub   # kiro-cli 無しで確認
kiro-marshal run --backlog backlog.md --dry-run             # verify だけで状態整合
kiro-marshal needs --backlog backlog.md                     # 人の判断待ちを表示
kiro-marshal approve <id> --reason "…"                      # 承認して積み直し（→ DECISIONS）
kiro-marshal hold <id> --reason "…"                         # 保留（policy.deny 追加 → DECISIONS）
```

終了コード: `0`=完走で判断待ち無し / `1`=判断待ちあり / `2`=予算停止。CI に組める。
人の判断待ちへの**遷移時だけ** `NEEDS_YOU.md`＋stdout に通知（毎サイクルでは鳴らさない）。
run 末尾で `done` は `ARCHIVE.md` へ自動退避し backlog を小さく保つ（`--no-archive` で無効化）。

## エージェントの振る舞い

- 「回して」と言われたら `kiro-marshal run` を起動し、停止後は
  **判断待ち（blocked）と停止理由を報告**する（勝手に done 扱いしない）。
- backlog にタスクを追加するときは**必ず実行可能な `verify` を付ける**。書けないなら分解が粗い兆候。
- 優先順位を機械に任せたくない時は `policy.md` に `deny`/`pin`/`defer` を書く（人間が必ず勝つ）。
- 曖昧で人間判断が要るタスクは積まずに確認する。ループは「機械的に検証できる作業」を回す箱。
