---
applyTo: "**/backlog/*.md"
---

# kiro-autonomous 規約 — backlog/・policy.md・needs/・decisions/

Loop Engineering の MVP。**`backlog/`（案件毎ファイル）を優先順位付けし、最優先タスクを kiro-flow に
実行させ、verify ゲートで検証し、done は archive/ へ退避・NG なら積み直す——backlog が尽きるか予算が
尽きるまで繰り返す**制御層。人の判断が要った分は案件毎の `needs/<id>.md`（フィードバック欄つき）で
差し出し、判断は `decisions/<id>.md` に残す。実体は `tools/kiro-autonomous/` と `tools/kiro-flow/`。
詳細設計は `docs/designs/2026-06-16-kiro-autonomous-mvp-design.md`。

## 正準ループ（5点）

1. backlog/<id>.md を読み優先順位をつけ、最優先を kiro-flow に投げる。
2. 優先順位付けは `--planner kiro`（エージェントが外部 `priority` も加味）/ `none`（priority 降順→最古）。
   人間は `policy.md` で上書きできる。
3. kiro-flow の結果を verify ゲートで検証。done は archive/ へ退避、NG なら積み直す。
   委譲方法は `--location`（local=run / daemon・remote=submit＋結果待ち。auto は daemon 有無/offload で解決）。
4. drained or 予算切れ（budget=サイクル数/実時間）まで反復。`--watch` なら以後も backlog/ を監視
   （idle 中はエージェントを起動しない）。
5. ユーザーの判断・フィードバックは案件毎 `decisions/<id>.md` に保存。

## backlog/<id>.md 規約（1ファイル＝1タスク。id はファイル名）

```markdown
## <id>: <タイトル>
- status: inbox | draft | ready | doing | done | blocked
- source: human | triage | followup
- priority: 0          # 外部で付与（大きいほど高優先。省略時 0）
- verify: `終了コード0をPASSとみなすシェルコマンド`
- retries: 0
- note: 任意（保持される）
```

- `ready`（実行待ち）を **priority 降順→同値は最古（mtime 昇順）** で消化（`--planner none`。
  `kiro` はエージェントが priority も加味）。`inbox` は triage で verify があれば
  `ready` に昇格、無ければ据え置き（acceptance 未定義として人へ）。
- **done になったファイルは `archive/<id>.md` へ退避**。`backlog/` には常に未完だけが残る
  （`--no-archive` で削除に切替）。

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
ファイルは cwd の **`./.kiro-autonomous/` 配下に集約**される（`--root` で変更可）。
kiro-flow バス等の一時状態は run 後に自動クリーンアップ（`--no-cleanup` で保持）。

## 実行

```bash
kiro-autonomous run --executor kiro                  # backlog/ を自律消化
kiro-autonomous run --watch --poll 10 --executor kiro  # 常駐（新規/フィードバック監視）
kiro-autonomous run --planner none --flow-planner stub --executor stub   # kiro-cli 無しで確認
kiro-autonomous needs                                # 人の判断待ちを表示
kiro-autonomous approve <id> --reason "…"            # 承認して積み直し（→ decisions/<id>.md）
kiro-autonomous hold <id> --reason "…"               # 保留（policy.deny 追加）
kiro-autonomous rot [--fix]                          # 古い/重複/実行不能を検出（--fix で人の判断へ）
```

終了コード（非 watch）: `0`=完走で判断待ち無し / `1`=判断待ちあり / `2`=予算停止。CI に組める。
**フィードバック往復**: 判断待ちは `needs/<id>.md` を生成。人が「## フィードバック」欄に記入し
`- [x] 確定` にすると次パスで拾われ、ブロック解除＋内容を次の act に反映する（書きかけ誤発火を防ぐため
チェックボックス必須・新規タスクは `status: draft` で保留・watch 中は `--debounce` 秒静穏化を待つ）。
**納品書**: done で `archive/<id>.md` に検収サマリー（verify=PASS・成果参照）を付し、`DELIVERY.md`
（受領書一覧）に1行追記する。**DR 学習**: 繰り返し NG で人へ回る前に過去 `decisions/` の類似指示を
自動適用して通知を抑制（`--no-learn` で無効）。**学習昇格**: `--ltm` 時、実績（auto-resolve で
`--promote-threshold` 回以上効いた）learn ルールを `ltm-use` home へ昇格し、別プロジェクトからも
横断 recall（決定的・エージェント不要・`promote` で手動実行）。**rot**: `run --rot` で古い/重複/実行不能を掃除。

## エージェントの振る舞い

- 「回して」と言われたら `kiro-autonomous run` を起動し、停止後は
  **判断待ち（blocked）と停止理由を報告**する（勝手に done 扱いしない）。
- backlog にタスクを追加するときは `backlog/<id>.md` を1ファイルで作り、**必ず実行可能な `verify` を
  付ける**。書けないなら分解が粗い兆候。
- 優先順位を機械に任せたくない時は `policy.md` に `deny`/`pin`/`defer` を書く（人間が必ず勝つ）。
- 曖昧で人間判断が要るタスクは積まずに確認する。ループは「機械的に検証できる作業」を回す箱。
