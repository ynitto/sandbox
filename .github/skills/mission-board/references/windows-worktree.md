# Windows での git worktree セットアップ手順

git worktree は Git for Windows でそのまま動作する。以下に PowerShell での具体的な手順を示す。

## 前提条件

- Git for Windows がインストール済みであること（`git --version` で確認）
- PowerShell でコマンドを実行できること

---

## 変数定義（PowerShell）

```powershell
$MISSIONS_BRANCH = "missions"
$WORKTREE_PATH = ".worktrees/missions"
```

---

## Worktree Setup 手順

### Step 1: 既存 worktree の確認

```powershell
git worktree list
```

出力例:
```
C:/Users/you/repos/myproject        abc1234 [main]
C:/Users/you/repos/myproject/.worktrees/missions  def5678 [missions]
```

### Step 2: missions ブランチの存在確認

```powershell
git ls-remote --heads origin $MISSIONS_BRANCH
```

### Step 3A: リモートに missions ブランチが存在する場合

```powershell
git worktree add $WORKTREE_PATH $MISSIONS_BRANCH
```

### Step 3B: リモートに missions ブランチが存在しない場合

orphan ブランチを作成して worktree を追加する:

```powershell
git worktree add --orphan -b $MISSIONS_BRANCH $WORKTREE_PATH
```

初期ファイルを作成してプッシュ:

```powershell
# テンプレートから初期ファイルをコピー
Copy-Item ".github/skills/mission-board/templates/GOAL.md" "$WORKTREE_PATH/GOAL.md"

# registry.md を作成（hostname を記載）
$hostname = hostname
@"
# Registry

| hostname | agent | role | status | last-seen |
| -------- | ----- | ---- | ------ | --------- |
| $hostname | $hostname | worker | 🟢 active | $(Get-Date -Format 'yyyy-MM-ddTHH:mm') |
"@ | Set-Content "$WORKTREE_PATH/registry.md"

# コミット & プッシュ
git -C $WORKTREE_PATH add GOAL.md registry.md
git -C $WORKTREE_PATH commit -m "chore: initialize mission board"
git -C $WORKTREE_PATH push -u origin $MISSIONS_BRANCH
```

---

## Preflight & Pull（PowerShell）

```powershell
# 未コミット変更を stash
$status = git -C $WORKTREE_PATH status --porcelain
if ($status) {
    git -C $WORKTREE_PATH stash push -u -m "autostash before pull"
}

# pull
git -C $WORKTREE_PATH pull origin $MISSIONS_BRANCH

# stash を戻す
if ($status) {
    git -C $WORKTREE_PATH stash pop
}
```

---

## Commit & Push（PowerShell）

```powershell
git -C $WORKTREE_PATH add missions/ GOAL.md registry.md
git -C $WORKTREE_PATH commit -m "feat: <description>"
git -C $WORKTREE_PATH push origin $MISSIONS_BRANCH
```

---

## Worktree の削除

不要になった worktree を削除する場合:

```powershell
git worktree remove $WORKTREE_PATH
```

強制削除（未コミット変更がある場合）:

```powershell
git worktree remove --force $WORKTREE_PATH
```

---

## トラブルシューティング

### `git worktree add` が失敗する

**原因**: `.worktrees/` ディレクトリが存在しない場合は Git が自動作成するため、通常は不要。ただし権限エラーが出る場合は手動作成:

```powershell
New-Item -ItemType Directory -Force -Path ".worktrees"
```

### `--orphan` オプションが使えない

Git for Windows 2.25.0 以降で対応。バージョン確認:

```powershell
git --version
```

古い場合は Git for Windows を更新する:
https://gitforwindows.org/

### パスに日本語・スペースが含まれる場合

パスをクォートで囲む:

```powershell
git worktree add ".worktrees/missions" $MISSIONS_BRANCH
```

### worktree が "locked" になっている

```powershell
git worktree unlock $WORKTREE_PATH
git worktree remove $WORKTREE_PATH
```
