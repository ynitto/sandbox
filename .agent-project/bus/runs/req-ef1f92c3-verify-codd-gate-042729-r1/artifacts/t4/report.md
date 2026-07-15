# verify-codd-gate-042729 / t4 検証レポート

## 判定
- **verify=fail**

## 完了条件コマンドの実行結果
- 実行コマンド:
  - `PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_REV\""'`
- 終了コード: `0`（成功）

## 敵対的入力の再導出検証

### 通過したケース
- ラベルのみでコマンド無し: `None`（期待どおり）
- 全角コロン `検証コマンド：...`: 抽出成功
- コードフェンス内にコマンド: 抽出成功
- コマンド行に末尾空白: 抽出成功
- 引用符が入れ子: 抽出成功
- 既存の素のコマンド行: 抽出成功（退行なし）
- 「ラベル前に散文がある（前行に散文、次行にラベル+コマンド）」: 抽出成功
- 「ラベルが2回（1行目がラベル単独、2行目がラベル+コマンド）」: 抽出成功

### 失敗したケース（要求不充足）
1. 同一行でラベルが2回出ると抽出不能
   - 入力:
     - `検証コマンド: 検証コマンド: codd-gate verify --base "$KIRO_BASE_REV"`
   - 期待値:
     - `codd-gate verify --base "$KIRO_BASE_REV"`
   - 実際:
     - `None`
   - 原因:
     - `_strip_leading_command_label()` が1回しか剥がさず、残った先頭トークンが `検証コマンド:` のままになり `_has_command_like_leading_token()` を通らない。

2. 同一行の散文プレフィックス + ラベルで抽出不能
   - 入力:
     - `以下を実行してください。検証コマンド: codd-gate verify --base "$KIRO_BASE_REV"`
   - 期待値:
     - `codd-gate verify --base "$KIRO_BASE_REV"`
   - 実際:
     - `None`
   - 原因:
     - ラベル除去が `^検証コマンド...` の行頭固定一致に依存しており、同一行先頭が散文だと剥がせない。

## ハードコード依存の確認
- `verify.py` の `_VERIFY_COMMAND_LABEL_RE = r"^検証コマンド\\s*[:：]\\s*"` は**日本語ラベルの行頭固定一致**に依存している。
- そのため、上記2件のような「同一行で前置きが混ざる」入力に脆弱。  
  一方で、素のコマンド行や従来の2行形式は退行していない。

## スコープ外変更混入チェック
- 現在のワークツリー上では対象2ファイル（`verify.py`, `test_agent_project.py`）に未コミット差分は見えず、追加の無関係差分は確認できなかった。
