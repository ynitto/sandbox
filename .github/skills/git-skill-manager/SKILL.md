---
name: git-skill-manager
description: Gitリポジトリを使ってエージェントスキルを管理するスキル。複数リポジトリの登録、スキルのpull（取得）とpush（共有）を行う。「スキルをpullして」「リポジトリからスキルを取ってきて」「スキルをpushして」「リポジトリを登録して」「スキル一覧」など、スキルの取得・共有・リポジトリ管理に関するリクエストで使用する。GitHub/GitLab/Bitbucket/セルフホスト問わず動作する。Copilot + Windows環境で動作し、gitは設定済みの前提。
---

# Git Skill Manager

Gitリポジトリ経由でエージェントスキルの取得（pull）と共有（push）を行う管理システム。

## 利用者

| 呼び出し元 | 操作 | 例 |
|---|---|---|
| ユーザー直接 | repo add / pull / search / list | 「スキルをpullして」「リポジトリを登録して」 |
| scrum-master サブエージェント | push | Phase 6 のスキル共有時にテンプレート経由で起動される |

- ユーザー直接呼び出しの場合、対話的に確認しながら進める
- サブエージェント経由の場合、プロンプトに必要な情報（対象スキル・リポジトリ名・操作）が含まれるため、確認なしで実行する

## 動作環境

- **Claude Code（Copilot）on Windows**
- git はインストール・認証設定済み（SSH鍵 or credential manager）
- シェルは PowerShell または cmd を想定。bashコマンドは使わない

-----

## アーキテクチャ

```
ローカル（Windows）
─────────────────────────────────────────
  %USERPROFILE%\.copilot\skills\          ← スキルインストール先
    ├── skill-a\SKILL.md
    ├── skill-b\SKILL.md
    └── ...

  %USERPROFILE%\.copilot\skill-registry.json  ← レジストリ
─────────────────────────────────────────
         │ pull              │ push
         ▼                   ▼
  ┌────────────────┐  ┌────────────────┐
  │ repo: team     │  │ repo: personal │
  │ (GitLab)       │  │ (GitLab)       │
  └────────────────┘  └────────────────┘
```

-----

## レジストリ

パス: `%USERPROFILE%\.copilot\skill-registry.json`

```json
{
  "version": 1,
  "repositories": [
    {
      "name": "team-skills",
      "url": "https://github.com/myorg/agent-skills.git",
      "branch": "main",
      "skill_root": "skills",
      "description": "チーム共有スキル集"
    }
  ],
  "installed_skills": [
    {
      "name": "docx-converter",
      "source_repo": "team-skills",
      "source_path": "skills/docx-converter",
      "commit_hash": "a1b2c3d",
      "installed_at": "2026-02-14T12:00:00Z"
    }
  ]
}
```

レジストリが存在しなければ初回操作時に自動作成する。

-----

## 操作一覧

|操作             |トリガー例               |
|---------------|--------------------|
|**repo add**   |「リポジトリを登録して」        |
|**repo list**  |「登録リポジトリ一覧」         |
|**repo remove**|「リポジトリを削除して」        |
|**pull**       |「スキルをpullして」「スキルを取得」|
|**push**       |「スキルをpushして」「スキルを共有」|
|**list**       |「インストール済みスキル一覧」     |
|**search**     |「リポジトリにあるスキルを探して」   |

-----

## パス定義

すべての操作で以下のパスを使う。

```powershell
$SKILL_HOME   = "$env:USERPROFILE\.copilot\skills"
$REGISTRY     = "$env:USERPROFILE\.copilot\skill-registry.json"
$TEMP_WORK    = "$env:TEMP\claude-skill-work"
```

初回は `$SKILL_HOME` ディレクトリを作成する:

```powershell
if (-not (Test-Path $SKILL_HOME)) { New-Item -ItemType Directory -Path $SKILL_HOME -Force }
```

-----

## repo add

```powershell
# 接続確認
git ls-remote $REPO_URL HEAD

# 成功したらレジストリに追加
```

```python
import json, os
from datetime import datetime, timezone

registry_path = os.path.join(os.environ["USERPROFILE"], ".copilot", "skill-registry.json")

def load_registry():
    if os.path.exists(registry_path):
        with open(registry_path, encoding="utf-8") as f:
            return json.load(f)
    return {"version": 1, "repositories": [], "installed_skills": []}

def save_registry(reg):
    os.makedirs(os.path.dirname(registry_path), exist_ok=True)
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)

def add_repo(name, url, branch="main", skill_root="skills", description=""):
    reg = load_registry()
    if any(r["name"] == name for r in reg["repositories"]):
        print(f"'{name}' は既に登録済みです")
        return
    reg["repositories"].append({
        "name": name,
        "url": url,
        "branch": branch,
        "skill_root": skill_root,
        "description": description,
    })
    save_registry(reg)
    print(f"✅ リポジトリ '{name}' を登録しました")
```

-----

## pull

### 処理フロー

```python
import subprocess, shutil, os, re, json, glob
from datetime import datetime

temp_work = os.path.join(os.environ["TEMP"], "claude-skill-work")
skill_home = os.path.join(os.environ["USERPROFILE"], ".copilot", "skills")

def pull_skills(repo_name=None, skill_name=None):
    """
    repo_name=None → 全リポジトリから取得
    skill_name=None → リポジトリ内の全スキルを取得
    """
    reg = load_registry()
    repos = reg["repositories"]
    if repo_name:
        repos = [r for r in repos if r["name"] == repo_name]
        if not repos:
            print(f"❌ リポジトリ '{repo_name}' が見つかりません")
            return

    os.makedirs(skill_home, exist_ok=True)

    # 全リポジトリからスキル候補を収集
    candidates = {}  # skill_name -> [{ repo, path, date, ... }]

    for repo in repos:
        clone_dir = os.path.join(temp_work, repo["name"])
        if os.path.exists(clone_dir):
            shutil.rmtree(clone_dir)

        subprocess.run([
            "git", "clone", "--depth", "1",
            "--branch", repo["branch"],
            repo["url"], clone_dir
        ], check=True)

        root = os.path.join(clone_dir, repo["skill_root"])
        if not os.path.isdir(root):
            continue

        for entry in os.listdir(root):
            skill_md = os.path.join(root, entry, "SKILL.md")
            if not os.path.isfile(skill_md):
                continue
            if skill_name and entry != skill_name:
                continue

            # コミット日時を取得
            result = subprocess.run(
                ["git", "log", "-1", "--format=%aI", "--",
                 os.path.join(repo["skill_root"], entry).replace("\\", "/")],
                cwd=clone_dir, capture_output=True, text=True
            )
            commit_date = result.stdout.strip() or "1970-01-01T00:00:00+00:00"

            commit_hash = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=clone_dir, capture_output=True, text=True
            ).stdout.strip()

            candidates.setdefault(entry, []).append({
                "repo_name": repo["name"],
                "source_path": os.path.join(repo["skill_root"], entry),
                "full_path": os.path.join(root, entry),
                "commit_date": commit_date,
                "commit_hash": commit_hash,
            })

    # 同名スキルの競合解決: コミット日時が新しい方を採用
    installed = []
    conflicts = []

    for sname, sources in candidates.items():
        if len(sources) > 1:
            sources.sort(key=lambda s: s["commit_date"], reverse=True)
            conflicts.append({
                "skill": sname,
                "adopted": sources[0]["repo_name"],
                "rejected": [s["repo_name"] for s in sources[1:]],
            })
        winner = sources[0]

        dest = os.path.join(skill_home, sname)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(winner["full_path"], dest)

        installed.append({
            "name": sname,
            "source_repo": winner["repo_name"],
            "source_path": winner["source_path"],
            "commit_hash": winner["commit_hash"],
            "installed_at": datetime.now().isoformat(),
        })

    # レジストリ更新
    existing = {s["name"]: s for s in reg.get("installed_skills", [])}
    for s in installed:
        existing[s["name"]] = s
    reg["installed_skills"] = list(existing.values())
    save_registry(reg)

    # クリーンアップ
    shutil.rmtree(temp_work, ignore_errors=True)

    # 結果レポート
    print(f"\n📦 pull 完了")
    print(f"   新規/更新: {len(installed)} 件")
    if conflicts:
        print(f"   競合解決:  {len(conflicts)} 件")
        for c in conflicts:
            print(f"     {c['skill']}: {c['adopted']} を採用（{', '.join(c['rejected'])} より新しい）")
    for s in installed:
        print(f"   ✅ {s['name']} ← {s['source_repo']} ({s['commit_hash']})")
```

-----

## push

### 処理フロー

```python
def push_skill(skill_path, repo_name, branch_strategy="new_branch", commit_msg=None):
    """
    skill_path: プッシュするスキルフォルダのパス
    repo_name: プッシュ先リポジトリ名（レジストリの name）
    branch_strategy: "new_branch" or "direct"
    """
    reg = load_registry()
    repo = next((r for r in reg["repositories"] if r["name"] == repo_name), None)
    if not repo:
        print(f"❌ リポジトリ '{repo_name}' が見つかりません")
        return

    skill_md = os.path.join(skill_path, "SKILL.md")
    if not os.path.isfile(skill_md):
        print(f"❌ SKILL.md が見つかりません: {skill_path}")
        return

    skill_name = os.path.basename(skill_path.rstrip("\\/"))
    clone_dir = os.path.join(temp_work, f"push-{repo_name}")
    if os.path.exists(clone_dir):
        shutil.rmtree(clone_dir)

    # clone
    subprocess.run([
        "git", "clone", "--depth", "1",
        "--branch", repo["branch"],
        repo["url"], clone_dir
    ], check=True)

    # ブランチ作成
    push_branch = repo["branch"]
    if branch_strategy == "new_branch":
        push_branch = f"add-skill/{skill_name}"
        subprocess.run(["git", "checkout", "-b", push_branch], cwd=clone_dir, check=True)

    # スキルをコピー
    dest = os.path.join(clone_dir, repo["skill_root"], skill_name)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(skill_path, dest)

    # 不要ファイル除外
    for pattern in ["__pycache__", ".DS_Store", "*.pyc", "node_modules"]:
        for match in glob.glob(os.path.join(dest, "**", pattern), recursive=True):
            if os.path.isdir(match):
                shutil.rmtree(match)
            else:
                os.remove(match)

    # commit & push
    if not commit_msg:
        commit_msg = f"Add skill: {skill_name}"

    subprocess.run(["git", "add", "."], cwd=clone_dir, check=True)

    # 変更があるか確認
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=clone_dir)
    if diff.returncode == 0:
        print("ℹ️ 変更がありません。プッシュをスキップします")
        shutil.rmtree(temp_work, ignore_errors=True)
        return

    subprocess.run(["git", "commit", "-m", commit_msg], cwd=clone_dir, check=True)
    subprocess.run(["git", "push", "origin", push_branch], cwd=clone_dir, check=True)

    commit_hash = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=clone_dir, capture_output=True, text=True
    ).stdout.strip()

    # クリーンアップ
    shutil.rmtree(temp_work, ignore_errors=True)

    print(f"\n🚀 push 完了")
    print(f"   スキル:     {skill_name}")
    print(f"   リポジトリ: {repo_name} ({repo['url']})")
    print(f"   ブランチ:   {push_branch}")
    print(f"   コミット:   {commit_hash}")
    if branch_strategy == "new_branch":
        print(f"   💡 PR/MR を作成してマージしてください")
```

-----

## list

```python
def list_skills():
    reg = load_registry()
    registry_map = {s["name"]: s for s in reg.get("installed_skills", [])}

    print(f"📂 スキル一覧 ({skill_home})\n")
    if not os.path.isdir(skill_home):
        print("   (スキルなし)")
        return

    for entry in sorted(os.listdir(skill_home)):
        if not os.path.isfile(os.path.join(skill_home, entry, "SKILL.md")):
            continue
        info = registry_map.get(entry, {})
        repo = info.get("source_repo", "local")
        hash_ = info.get("commit_hash", "-")
        print(f"   {entry:30s}  repo: {repo:20s}  commit: {hash_}")
```

-----

## search

```python
def search_skills(repo_name=None, keyword=None):
    reg = load_registry()
    repos = reg["repositories"]
    if repo_name:
        repos = [r for r in repos if r["name"] == repo_name]

    for repo in repos:
        clone_dir = os.path.join(temp_work, repo["name"])
        if os.path.exists(clone_dir):
            shutil.rmtree(clone_dir)
        subprocess.run([
            "git", "clone", "--depth", "1",
            "--branch", repo["branch"],
            repo["url"], clone_dir
        ], check=True)

        root = os.path.join(clone_dir, repo["skill_root"])
        if not os.path.isdir(root):
            continue

        print(f"\n🔍 {repo['name']} ({repo['url']})")
        found = False
        for entry in sorted(os.listdir(root)):
            skill_md = os.path.join(root, entry, "SKILL.md")
            if not os.path.isfile(skill_md):
                continue
            with open(skill_md, encoding="utf-8") as f:
                content = f.read()
            desc = ""
            match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
            if match:
                for line in match.group(1).splitlines():
                    if line.startswith("description:"):
                        desc = line[len("description:"):].strip()
                        break

            if keyword and keyword.lower() not in entry.lower() and keyword.lower() not in desc.lower():
                continue

            found = True
            short_desc = desc[:80] + "..." if len(desc) > 80 else desc
            print(f"   {entry:30s}  {short_desc}")

        if not found:
            print("   (該当なし)")

    shutil.rmtree(temp_work, ignore_errors=True)
```

-----

## エラーハンドリング

|エラー               |対処                         |
|------------------|---------------------------|
|`git ls-remote` 失敗|URL・認証を確認するよう案内            |
|clone 失敗          |ブランチ名を `git ls-remote` で確認 |
|push rejected     |`git pull --rebase` 後に再push|
|SKILL.md なし       |スキルフォルダの構成確認を案内            |
|レジストリ破損           |削除して再作成するか、リポジトリから再pull    |
|ネットワークエラー         |ネットワーク接続を確認するよう案内          |

-----

## 使用例

### 初回セットアップ

```
ユーザー: 「https://github.com/myorg/skills.git をスキルリポジトリに登録して」

Claude:
  1. git ls-remote で接続確認
  2. レジストリ作成、リポジトリ追加
  3. 「登録しました。pullしますか？」
```

### pull

```
ユーザー: 「スキルを全部同期して」

Claude:
  1. 全リポジトリを shallow clone
  2. 各リポジトリのスキルを走査
  3. 同名競合はコミット日時で新しい方を採用
  4. %USERPROFILE%\.copilot\skills\ にコピー、レジストリ更新
  5. 結果レポート
```

### push

```
ユーザー: 「今作ったスキルを team-skills にpushして」

Claude:
  1. レジストリから team-skills の情報を取得
  2. SKILL.md の存在確認
  3. clone → ブランチ作成 → コピー → commit → push
  4. コミットハッシュとブランチ名を報告
```
