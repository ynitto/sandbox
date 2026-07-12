# `_first_command_line` 入出力契約（呼び出し経路の追跡）

対象: `tools/kiro-project/kiro-project.py:2953`

## 呼び出しグラフ

```
synth_verify(cfg, title, accept, kiro_run, hint, repo_ctx, attempts=2)   L2968
  └─ for _ in range(attempts):
       out = run(prompt, cfg.model)            # kiro-cli 呼び出し。例外あり
       cand = _first_command_line(out)         # L2982 ここが対象関数
       ...（下記フィルタ）...
       return cand  # 全フィルタ通過時のみ

synth_verify の呼び出し元は2つ:
  1. ensure_verify(cfg, task, kiro_run)          L2996-3033
     └─ plan() が backlog 走査中に呼ぶ            L5046
  2. resolve_charter_acceptance(cfg, charter, state, kiro_run)  L8976-9002
```

## `_first_command_line` 単体の入出力

- 入力: `out: str` — kiro-cli（LLM）が返した生テキスト。前置き文・コードフェンス・コメント等を含みうる。
- 出力: `Optional[str]`
  1. コードフェンス（` ``` `）があれば、フェンス内の最初の非空・非コメント行を**無条件で**返す（`require_shell_syntax=False`、`sh -n` 構文チェックなし）。
  2. フェンスが無ければ、フェンス外の行のうち `_has_command_like_leading_token`（既知コマンド語 / `./` `../` `/` 始まり / `foo-bar` 形のハイフン付きCLI名）に合致し、かつ `_looks_like_shell_command`（`sh -n` 構文チェック）を通る最初の行を返す。
  3. どちらにも合致する行が無ければ `None`。
- 例外: 送出しない（正規表現・文字列操作のみ）。

## `synth_verify` 内での戻り値の消費（L2982-2991）

| `_first_command_line` の戻り値 | 後続フィルタ | synth_verify の挙動 |
|---|---|---|
| `None` | — | `retry_note = "応答に実行可能なコマンド行がなかった"` → 次 attempt へ `continue` |
| 文字列だが `_looks_like_shell_command(cand)` が False | 自然言語（説明・拒否文）と判定 | `retry_note = "シェルコマンドでなかった"` → `continue` |
| 文字列で shell 構文は妥当だが `_verify_is_degenerate(cand)` が True | `true` / `echo ...` 等の恒真式 | `retry_note = "恒真式に退化していた..."` → `continue` |
| 上記3条件をすべて通過 | — | `return cand`（synth_verify の戻り値として即 return） |

- `attempts`（既定2）を使い切って全滅した場合、stderr に `[kiro-project] verify 合成失敗: {retry_note}（task: {title}）` を出力し、`synth_verify` は `""`（空文字）を返す。
- `run(...)`（kiro-cli 呼び出し自体）が例外を送出した場合（kiro-cli 不在・タイムアウト等）は `_first_command_line` に到達する前に `except Exception: return ""` で捕捉され、即座に空文字を返す。`_first_command_line` はこの経路には関与しない。

## 空文字（`""`）が上位呼び出し元でどう扱われるか

### `ensure_verify`（タスクの `accept` から concrete verify を合成）
- `cmd = synth_verify(...)`; `if cmd:` が偽 → `task.verify` は**セットされず**、関数は `False` を返す。
- 呼び出し元 `plan()`（L5046）: `if t.norm_status() in CONSUMABLE and not t.verify and ensure_verify(cfg, t):` が偽になるため、`persist_task` も `append_journal(... "verify 用意: ...")` も**呼ばれない**。
- 結果: タスクは verify 未確定のまま backlog に残る。`t.verify` が空である限り、次回の `plan()` サイクルで `ensure_verify` が再試行される（無限リトライ、即座の needs 化はこの関数内には無い）。
- 注記: `synth_verify` のdocstringは「失敗・不能・kiro-cli 不在は空文字（→ verify 未定義のまま人へ）」と書いているが、コード上は `ensure_verify` が空文字を「今回は用意できなかった」として黙って `False` を返すのみで、この関数内に人（needs）へ即座に回す処理は無い。人への到達は `has_verify_plan`（L3036）や他の needs 生成経路（例: レビュー/rot 検知）に委ねられている。

### `resolve_charter_acceptance`（charter の受入条件の自然言語行を合成）
- `cmd = synth_verify(...)`; `if cmd:` が偽 → 元の自然言語テキスト `text` が `unresolved` リストへ積まれる（`resolved` には積まれない）。
- 関数は `(resolved, unresolved)` のタプルを返す。docstringに明記の通り、`unresolved` は「呼び出し側が done 判定不能として人へ回す」契約になっている（本関数自体は人への通知を行わない）。

## 契約まとめ

- **成功**: フェンス内 or フェンス外のコマンド候補が「自然言語でない」かつ「恒真式でない」を満たせば、`synth_verify` はその1行をそのまま verify コマンドとして返す。
- **失敗（コマンド候補なし／自然言語／恒真式）**: 例外にはならず、`retry_note` を伴う再試行 → 最終的に `synth_verify` が `""` を返す設計。
- **kiro-cli 呼び出し自体の失敗**: `_first_command_line` は関与せず、`synth_verify` が即座に `""` を返す。
- **`""` の下流影響**: `ensure_verify` 経由では「今サイクルは verify 未確定のまま静かに持ち越し」、`resolve_charter_acceptance` 経由では「該当行が `unresolved` に積まれ、呼び出し側が人へ回す」の2通りに分岐する。`_first_command_line` 自体は None/例外いずれも起こさないため、今回の不具合（コードフェンス内コマンドの拾い漏れ）は「LLM出力の前置きに紛れて `None` を返し retry を消費し尽くす → 最終的に verify 合成が `""` に落ちる」という経路で顕在化する。
