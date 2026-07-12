# t11: パス解決（/tmp ⇔ /private/tmp）欠陥の調査・対応

## (a) 成果サマリー — 結論: コード変更は不要と判断し、変更を行わなかった

タスクの前提「kiro-flow の git 自己修復ロジックが/tmp・/private/tmpのシンボリックリンク
不一致で誤動作する」を検証したが、**現在の worktree にはその欠陥が存在しない**ことを確認した。
理由は以下の3点。

1. **`tools/kiro-flow/kiro-flow.py` の `GitBus`/`StateGit`（git自己修復ロジック）は、
   パスを比較する箇所すべてで両辺を `os.path.realpath()` で正規化してから比較している**
   （`Path.resolve()` と機能的に同等）。該当箇所:
   - `GitBus._git_env`（L1126）: `GIT_CEILING_DIRECTORIES` 算出
   - `GitBus._is_own_repo_root`（L1239）: `os.path.realpath(top) == os.path.realpath(self.workdir)`
   - `GitBus._origin_matches`（L1243-1244）: `os.path.realpath(origin) == os.path.realpath(self.remote)`
   - `StateGit._env`（L1520）: 同上パターン
   - `StateGit._is_managed`（L1609, L1612-1613）: 同上パターン

   これらは既に `/tmp` と `/private/tmp`（macOSのシンボリックリンク）が指す実体が
   一致するかどうかで比較しており、生パス文字列の直接比較は行っていない。

2. **依存タスク t4 の `classification.json`** が、対象4件の失敗テストを実コードの
   grep 結果に基づき一次証拠で分類した結果、4件とも `primary_class: "d"`
   （ファイルモード/権限、`_zero_loose_objects()` の chmod 欠落）であり、
   `"category a"`（パス解決/シンボリックリンク）は **「関与を裏付ける証拠がコード上に
   見当たらない」として明示的に除外**されている。パス解決は当初の仮説カテゴリの
   1つだったが、実測では的中しなかった。

3. **完了条件コマンドは本タスク着手前から既に green**（t1/t3/t9/t10がいずれも
   `900 passed, exit 0` を確認済み）であり、本タスクでも下記(b)で独立に再確認した。

同種の symlink 正規化パターンは同一プロダクト内の `tools/kiro-project/kiro-project.py`
（`local キーは realpath で canonical 化`、L3465 コメント）や、その対応テスト
`tools/kiro-project/tests/test_kiro_project.py:2853`（`/tmp→/private/tmp` の照合ズレに
言及するコメント付き）でも既に確立・運用済みであり、この種の不一致は本プロダクトでは
既知パターンとして realpath 正規化で一貫して対処されている。

**未採用の選択肢**: `os.path.realpath()` を `pathlib.Path.resolve()` へ書き換える
純粋なスタイル置換も検討したが、挙動が同一（両者ともシンボリックリンクを解決する）で
バグ修正効果が無く、対象4件の失敗にも無関係なため、範囲外の「ついで修正」と判断し
見送った（karpathy-guidelines: 動いているものを直さない／最小差分の原則、および
本タスクの「範囲を守る」約束に基づく判断）。

## (b) 検証内容と結果

- `git status --short`（worktree）: 差分なし（コード変更なし）
- 完了条件コマンド再実行:
  ```
  python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q
  ```
  → **900 passed, exit code 0**（実行時間 121.51s）
- `grep -rn "os.path.realpath\|Path(" tools/kiro-flow/kiro-flow.py` で自己修復ロジック内の
  全パス比較箇所を洗い出し、正規化漏れがないことを目視確認。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 依存タスク t10 の統合計画（`_zero_loose_objects()` への `os.chmod` 1行追加、
  コミット `0cf9c59` で適用済み）を正とし、本タスクは「パス解決」という別仮説の
  検証・要否判断を担当すると解釈した。
- **未解決事項**: なし。
- **範囲外で見つけた問題**: `GitBus`/`StateGit` の自己修復ロジックがほぼ同型のまま
  重複実装されている点（t3の指摘と同じ）。統合すれば保守性が上がるが本タスクの
  範囲外。
- **機密情報**: 成果物に含めていない。

## 結論

対象の「パス解決欠陥」は現在の worktree には存在せず、修正は不要。完了条件は
コード変更なしで満たされている（900 passed, exit 0）。
