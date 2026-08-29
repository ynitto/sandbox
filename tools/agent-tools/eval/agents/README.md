# 評価専用の CLI 定義

ここに置く定義は**同梱しない**（`agents/` の同梱定義は 8 件のまま）。対照実験のためだけに
存在し、運用の候補にはならないからである——`agents/` へ置くと `agent-herd defs` にも
おすすめ構成の一族にも現れ、実測の裏付けが無いものが運用の選択肢に見える。

| 定義 | 何のため |
|---|---|
| `selfedit.json` | aider を使わない編集適用の対照実装（設計 2026-08-27 §3.6・未決 5）。`worker_eval --cli selfedit` が使う |

`worker_eval` は `--cli selfedit` のときだけ `KIRO_AGENTS_DIR` をここへ向ける。探索順の
先頭に足すだけなので、`agents/aider.json` など同梱定義はそのまま解決できる。
