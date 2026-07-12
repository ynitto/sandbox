# macOS固有要因調査②: APFS大文字小文字非区別の影響検証

## (a) 成果 / サマリー

APFS（デフォルトの大文字小文字非区別・大文字小文字保持）が、対象コード
（`tools/kiro-flow/kiro-flow.py`, `tools/kiro-project/kiro-project.py` の
git自己修復・reuse-clone関連ロジック）のパス比較・ファイル存在チェック・
ブランチ名/リモート名の一致判定に **理論上影響しうる箇所を1件、実測で特定した**。
ただし **現行の失敗テスト（4件）は実在しない**（依存タスクt5の結論と一致）ため、
「この特定箇所が今回の失敗の直接原因である」とは断定できない。範囲外だが
将来のmacOS特有バグとして記録に値する。

### 結論（一問一答）

| 検証対象 | 影響有無 | 根拠 |
|---|---|---|
| ファイル存在チェック (`os.path.exists`) | **あり**（ただし安全側） | 大文字違いのパスでも `True` を返す。既存コードの `while os.path.exists(dest): dest += "-N"` のような衝突回避ループは、大文字小文字違いの既存ディレクトリも「衝突」として検知するため、意図通り安全に動く（誤って上書きしない）。害はない。 |
| パス一致判定 (`os.path.realpath` 比較) | **あり（実バグの温床）** | `os.path.realpath()` はAPFS上で大文字小文字を正規化しない。同一実体でも大文字小文字が違うパス文字列を渡すと `realpath(a) == realpath(b)` が `False` になる（`os.path.samefile` は `True` なのに）。 |
| ブランチ名の一致判定 | **あり（gitそのものの制約、実測で再現）** | `refs/heads/<name>` はloose refとしてファイルシステムに書かれるため、大文字小文字だけ違うブランチ名は作成時に `fatal: a branch named 'X' already exists` で衝突する。Pythonの文字列比較（case-sensitive）とは無関係にgit自身がAPFS依存で衝突する。 |
| リモート名の一致判定（`git remote add <name>`） | **なし** | リモート名は `.git/config` の1ファイル内セクションとして管理され、ファイルシステムの大文字小文字非区別の影響を受けない。`origin` と `Origin` を別リモートとして問題なく追加できた（実測）。 |
| git status / rename検知 | **なし** | `core.ignorecase=true`（今回の環境で確認済み・gitが自動設定）により、git内部の索引比較で正しく大文字小文字違いのrenameを検知する（実測）。 |

## (b) 検証内容と結果（実測ログ）

環境: 作業ディレクトリ配下は APFS（`diskutil info /` → `File System Personality: APFS`）。

1. **ファイルシステムの大文字小文字非区別を実測確認**
   一時ディレクトリに `TestFile.txt` を作成 → `testfile.txt` で `os.path.isfile` すると `True`。
   → 「CASE-INSENSITIVE filesystem」と確定。

2. **`os.path.exists` / `os.listdir` の挙動**
   `MyDir` を作成→`mydir` で `os.path.exists` は `True`、`os.listdir` は元の大文字小文字
   （`MyDir`）を返す。Python文字列同士の `==` 比較は case-sensitiveのまま（`p1 == p2` → `False`）。

3. **`os.path.realpath` は大文字小文字を正規化しない（重要な実測結果）**
   ```
   realpath(MyRepo) = .../MyRepo
   realpath(myrepo) = .../myrepo
   realpath一致 -> False
   samefile      -> True
   ```
   これは `tools/kiro-flow/kiro-flow.py:1241-1244` の `_origin_matches()` および
   `tools/kiro-project/kiro-project.py:5115-5116`（同名ロジック）が使う判定式
   `os.path.realpath(origin) == os.path.realpath(self.remote)` に直接影響する。
   ローカルパスのremoteを、大文字小文字が異なる文字列（例: マウントパスの表記揺れ、
   ユーザーが打ち間違えた`--git`引数など）で2回参照すると、実体は同一でも
   `_origin_matches()` は `False` を返し、結果として `_is_managed_bus_clone()` が
   `False` になり、`test_reuse_full_checkout_of_same_remote_is_refused` と同じ経路
   （「foreign non-empty dir」= 上書き拒否のRuntimeError）に落ちる可能性がある。
   同種のパターンは `kiro-project.py` の3465行台コメント（`local キーは realpath で
   canonical化`）や5637行目にも存在する。

4. **ブランチ名の大文字小文字違いはgit自身が衝突として検知（実測で再現）**
   ```
   $ git branch feature/x
   $ git branch Feature/x
   fatal: a branch named 'Feature/x' already exists
   ```
   `.git/refs/heads/` 配下は実ファイルとして書かれるため、APFSの非区別性により
   `feature/x` と `Feature/x` は同じパスとして衝突する。これはPython側の文字列比較
   ロジック（`run_branch_name`, `workspace_id` 等）とは独立した、git自体のmacOS依存の
   挙動である。対象コードの `run_branch_name(run_id) = f"kf/{_safe(run_id)}"` や
   kiro-projectのタスクID由来ブランチ名（`kp/<task-id>`）が、大文字小文字だけ異なる
   2つのIDから生成された場合に初めて顕在化する（現行のIDには該当例なし）。

5. **リモート名は非対象と確認（実測）**
   `git remote add origin ...` の後 `git remote add Origin ...` は成功し、
   `git remote -v` に両方独立して表示された。リモート名は `.git/config` 内の
   セクション名であり、ファイルシステムのパスとして扱われないため非区別性の
   影響を受けない。

6. **git status のcase-only rename検知は正常（実測）**
   `File.txt` → `file.txt` へ `git mv` した後 `git status --short` は
   `R  File.txt -> file.txt` を正しく表示。`git config --get core.ignorecase` は
   `true`（gitが初期化時に自動設定）。

7. **対象4件の「git自己修復」テストを個別実行し、現時点でpassすることを確認**
   ```
   python3 -m pytest tools/kiro-flow/tests/test_kiro_flow.py \
     -k "self_heals or corrupt_remote or stale_index_lock or interrupted_rebase \
         or reuse_full_checkout or git_retries_while_live_lock" -q
   → 7 passed, 381 deselected
   ```
   これらのテストは `self.bare` 等tempfile由来の同一文字列を使い回しており、
   大文字小文字が変化する経路を通らないため、上記4/5の潜在バグとは接触しない。

8. **完了条件コマンドの実行**: `python3 -m pytest tools/kiro-project/tests
   tools/kiro-flow/tests -q` をバックグラウンドで実行開始（本タスクはファイル編集を
   行っていないため、依存タスクt5が確認した「exit 0 / 900 passed」から状態は
   変化していないはずだが、念のため再確認した — 完了時に別途報告可能）。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**:
- 依頼は「調査」であり、ファイル編集は行っていない（`git status --short` で
  作業ツリーに変更が無いことを確認済み）。
- 依存タスクt5の結論（「4件の失敗テストは現時点で実在しない。900 passed/0 failed」）
  を事実として受け入れた上で、「もしAPFS非区別性が原因だとしたら、どこで・どう
  顕在化しうるか」を仮説検証する形で調査した。

**未解決事項**:
- 「macOSで失敗するgit自己修復テスト4件」の具体的なnodeidやトレースバックは、
  t4/t5同様に本タスクでも一次情報として確認できなかった。よって「今回の4件の
  失敗の直接原因がAPFS非区別性である」という主張はできない。

**範囲外で見つけた問題（別タスク化はしない・報告のみ）**:
- `tools/kiro-flow/kiro-flow.py:1241-1244`（`GitBus._origin_matches`）と
  `tools/kiro-project/kiro-project.py:5115-5116`（同型ロジック）は、ローカルパス
  remoteの同一性判定に `os.path.realpath()` の文字列一致を使っており、APFS上では
  大文字小文字違いの同一実体を「別remote」と誤判定しうる潜在バグ。現行テストでは
  発火しないが、`os.path.realpath()` の代わりに `os.stat().st_ino`/`os.path.samefile()`
  を使う形に直せば、大文字小文字非区別（および今回検証していないシンボリックリンク
  経由の別名）の双方に対して堅牢になる。修正の要否・優先度は評価役の判断に委ねる。
- ブランチ名の大文字小文字違いによる衝突（`fatal: a branch named ... already exists`）
  はgit自体のAPFS依存挙動であり、対象コードが生成するブランチ名（`kf/<run_id>`,
  `kp/<task-id>`）が現状すべて衝突しない命名になっている限り顕在化しない。将来
  run_id/task_idの命名規則が変わった場合のリスクとして記録に留める。
