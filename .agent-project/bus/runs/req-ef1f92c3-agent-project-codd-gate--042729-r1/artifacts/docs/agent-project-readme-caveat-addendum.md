# tools/agent-project/README.md への追記案（任意・小規模）

適用先: `tools/agent-project/README.md`
挿入位置: 230-236行目の「一貫性ゲート（codd-gate 連携・オプション）」パラグラフの末尾
（`...受入の負債ラチェット）。` の直後、`### policy.md` 見出しの直前）に1文追加する。

---

追加する1文（既存パラグラフの末尾に続ける）:

> なお、この静的な設定結線は codd-gate の実在・バージョンを検出しておらず、
> 未インストール／非互換の環境でこのまま `regression_cmd`/`intake_cmd` を有効化すると、
> 自動で無効化されるのではなく該当タスクが失敗として人へ回る（`tools/agent-project/
> codd_gate_status.py` の検出・no-op 縮退はランタイムへ未結線。
> 詳細は [`codd-gate-design.md`](../../docs/designs/codd-gate-design.md) §4.1）。

## 補足

主たる成果は `codd-gate-design-detection-addendum.md` 側。この README 追記は
運用者が「設定するだけで安全に有効化できる」と誤解しないための最小限の注意書きであり、
必須ではない（評価役が不要と判断すれば見送ってよい）。
