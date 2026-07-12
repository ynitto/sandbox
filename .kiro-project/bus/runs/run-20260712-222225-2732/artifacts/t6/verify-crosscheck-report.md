# synth_verify-_first_comm-172544 verify クロスチェック結果

判定: **verify=fail**

## 独立再導出の結果

- 完了条件コマンド `python3 -m pytest tools/kiro-project/tests -q -k first_command_line` は実行して `12 passed, 512 deselected`（exit 0）。
- `pytest -k first_command_line --collect-only` の選択結果は 12 件で、t3 の列挙と一致。
- 実装（`kiro-project.py`）を直接 import して再実行したところ、t4 の主張どおり以下は現行ロジックで再現:
  - フェンス外 `$ pytest -q` は `None`
  - フェンス内 ```` ```bash\n$ python3 -m pytest ...\n``` ```` は `'$ python3 -m pytest ...'`
  - フェンス外 `1. python3 -m pytest ...` は `None`
  - フェンス内 ```` ```bash\n1. pytest -q\n``` ```` は `'1. pytest -q'`

## 矛盾・抜け（重大のみ）

1. **戻り値契約と呼び出し側期待の不一致（実装内の契約不整合）**
   - どこで: `tools/kiro-project/kiro-project.py` `synth_verify` docstring（L2971）と `ensure_verify` 実装（L3028-L3033）
   - 何が: docstring は「失敗時は空文字（→ verify 未定義のまま人へ）」と読めるが、`ensure_verify` 側は `cmd` が空でも単に `False` を返して次サイクルへ持ち越すだけで、この経路単体では即時に人手回付しない。
   - どう直すべきか:  
     - 期待を「即時人手回付」ではなく「未確定のまま再試行・必要時に別ゲートで人へ」に統一するなら docstring/設計文言を更新。  
     - 逆に docstring を正にするなら `ensure_verify` 失敗時に needs 生成などの明示的回付処理を追加。

2. **t4 が示した実失敗カテゴリがテスト選択集合に未カバー（検証ギャップ）**
   - どこで: `tools/kiro-project/tests/test_kiro_project.py`（`-k first_command_line` で選ばれる 12 件）
   - 何が: `$ ` プレフィックスと番号付きリスト（`1. ` / `1) `）の失敗再現ケースが、`-k first_command_line` の対象テストに存在しない。
   - どう直すべきか: `TestVerifyAssist` に `test_first_command_line_*` 命名で上記4系統（フェンス内/外 × `$`/番号）を追加し、少なくとも「現在は失敗する」期待を明示するか、修正実装と同時に「正しく剥がす」期待へ更新して回帰化する。

## 補足

- t1/t2/t3/t4 の事実関係（現行実装・完了条件達成・12件選択・t4失敗再現）自体は相互に矛盾しない。
- fail 判定理由は「タスク指定の検査観点で検出された重大な契約不整合と検証抜け」が残っているため。
