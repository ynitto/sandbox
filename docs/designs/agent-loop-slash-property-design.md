# agent-loop — 定期プロンプトの `slash` プロパティ設計

> 作成 2026-08-06
> 対象: agent-loop（および fork 元/先の agent-loop 系プロジェクト）
> 関連: [2026-08-06 opencode×ollama CPU 推論の対策案](../plans/2026-08-06-opencode-ollama-cpu-inference-proposals.md) §F-2 補遺 2
>
> **この文書は fork 先へ単体で展開できるよう自己完結で書く。** 親文書を読まなくても
> 実装できることを意図している。
>
> **このリポジトリの agent-loop では実装済み**（2026-08-06）。実装は
> `tools/agent-loop/agent_loop/scheduler.py`（`_normalize_slash` と `_dispatch_prompt`）、
> テストは `tools/agent-loop/test/test_slash_property.py`。fork 先へは §3 の移植ガイドを使う。

---

## 1. 目的

定期プロンプトの設定エントリに、**送信文の前にスラッシュコマンドとして何を送るか**を
宣言する専用プロパティ `slash` を追加する。

動機は 2 つ:

1. **本文とコマンド指定の分離。** スラッシュコマンド（スキル呼び出し・モード切替など、
   対話 CLI が `/name` 形式で受けるもの）を使いたいとき、現状はプロンプト本文へ
   手で `/name` を書き込むしかない。本文と制御指定が混ざり、コマンドだけ変える・
   外すといった変更がしづらい。
2. **CLI 非依存の共通口。** 送るものは「先頭のスラッシュ行」というただのテキスト
   なので、スラッシュコマンドを解するどの対話 CLI（kiro-cli / claude / agent-ollama の
   TUI 等）に対しても同じプロパティが機能する。特定 CLI のための機構にしない。

## 2. 仕様

### 2.1 設定スキーマ

`prompts` の各エントリに任意プロパティ `slash` を追加する。

```yaml
prompts:
  - name: "ログ要約"
    slash: summarize-logs            # 文字列 1 本
    prompt: "昨日のログを要約して"
    interval_minutes: 60

  - name: "定期点検"
    slash: ["healthcheck", "report --lang ja"]   # 配列・引数付きも可
    prompt: "結果を3行で"
    interval_minutes: 240

  - name: "コマンドだけ定期実行"
    slash: compact                   # prompt 無しの slash 単独も許す
    interval_minutes: 120
```

- 型: `string | string[]`。文字列は要素 1 個の配列と等価。
- 各要素の形式: `<name>[ <args...>]`。`name` は `^[a-z0-9][a-z0-9._-]*$` に一致する
  こと（先頭 `/` は**書かない**。付いていたら剥がして警告 1 回）。args は自由文字列。
- 検証に落ちた要素は**そのエントリごと無効化せず**、要素だけ捨てて警告ログを出す
  （定期駆動を 1 文字のタイポで止めない）。

### 2.2 送信時の挙動

ディスパッチ時、`slash` の各要素を `/` 前置きの**独立した送信**として本文より先に
送る。1 本のテキストへ連結しない。

```
（fresh_context 有効時: "/clear" ）      ← 既存挙動。slash より先
"/summarize-logs"                        ← slash[0]
"/report --lang ja"                      ← slash[1]
"昨日のログを要約して"                    ← prompt 本文（あれば）
```

- **独立送信にする理由**: kiro-cli / claude 等の対話 CLI はスラッシュコマンドを
  「1 入力 = 1 コマンド」で解釈する。連結すると本文の一部として扱われ、機能しない。
  既存の `fresh_context` が `/clear` を本文と別の send で送っているのと同じ形に
  そろえる（実装もその経路を再利用する）。
- 順序: `/clear`（fresh_context）→ `slash` 要素を宣言順 → 本文。
- `prompt` が空で `slash` だけのエントリも有効とする。既存の「prompt は必須
  （event_hook があるときを除く）」の検証を「prompt / slash / event_hook の
  いずれかがあれば有効」へ緩める。
- `event_hook` 併用時は、hook が発火して本文を送る回にだけ slash も前置する
  （素振りの回には何も送らない）。

### 2.3 後方互換

- `slash` 未指定のエントリの挙動は 1 ビットも変わらない。
- 旧バージョンの agent-loop は未知キーを無視して読む（エントリは
  素の dict 取り回し）ため、**新しい設定ファイルを旧実装に食わせても壊れない**
  （slash が送られないだけ）。厳格なスキーマ検証を足している fork は、
  許可キー一覧へ `slash` を足すこと。

## 3. 実装ポイント（移植ガイド）

変更は 2 ファイル・計 30 行前後。fork 先でもファイル名は多少違えど同じ責務分割の
はず（scheduler = エントリ解釈と定期発火 / session = tmux send-keys）。

1. **エントリ解釈**（agent-loop では `agent_loop/scheduler.py` の設定読み込み部）:
   - `entry.get("slash")` を読み、`string | string[]` を正規化 → 検証 →
     `list[str]`（`/` 前置き済みの送信行）としてエントリへ保持。
   - 「prompt 必須」の検証条件へ slash を加える（§2.2）。
2. **ディスパッチ**（同 `_dispatch_prompt` 相当）:
   - fresh_context の `/clear` 送信の直後・本文送信の直前に、保持した slash 行を
     宣言順に 1 行ずつ `send_prompt`（tmux send-keys 経路）で送る。
   - 送信間の待ち・リトライは既存の `/clear` → 本文と同じ扱いに合わせる。
3. **テスト**: (a) string / list の正規化、(b) 不正要素の除外と警告、(c) 送信順
   （clear → slash → 本文）、(d) slash 単独エントリが有効、(e) 未指定エントリの
   挙動不変、の 5 点。

## 4. 決めたこと・決めなかったこと

- **決めた**: プロパティ名は `slash`（「先頭スラッシュ行を送る」という機構の名前。
  `skill` にしなかったのは、送り先の CLI にとってスキル呼び出しとは限らないから）。
- **決めた**: 連結ではなく独立送信（§2.2）。
- **決めなかった**: `>` プロンプトの `prompt-add` コマンドからの slash 指定
  （設定ファイル直編集で足りる。要望が出たら `prompt-add --slash <name>` を検討）。
- **決めなかった**: slash 行に対する CLI 側の応答待ち。既存の `/clear` と同様、
  応答を待たずに次を送る。コマンドの完了同期が要るユースケースが出たら別途。
