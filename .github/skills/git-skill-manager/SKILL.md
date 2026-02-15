---
name: git-skill-manager
description: Gitリポジトリを使ってエージェントスキルを管理するスキル。複数リポジトリの登録、スキルのpull（取得）とpush（共有）、スキルの有効化/無効化、プロファイル管理を行う。「スキルをpullして」「リポジトリからスキルを取ってきて」「スキルをpushして」「リポジトリを登録して」「スキル一覧」「スキルを無効化して」「プロファイルを切り替えて」など、スキルの取得・共有・リポジトリ管理・有効化管理に関するリクエストで使用する。GitHub/GitLab/Bitbucket/セルフホスト問わず動作する。Copilot + Windows環境で動作し、gitは設定済みの前提。
---

# Git Skill Manager

Gitリポジトリ経由でエージェントスキルの取得（pull）と共有（push）を行う管理システム。

## 利用者

| 呼び出し元 | 操作 | 例 |
|---|---|---|
| ユーザー直接 | repo add / pull / search / list / enable / disable / profile | 「スキルをpullして」「リポジトリを登録して」「スキルを無効化して」 |
| scrum-master サブエージェント | push | Phase 6 のスキル共有時にテンプレート経由で起動される |

- ユーザー直接呼び出しの場合、対話的に確認しながら進める
- サブエージェント経由の場合、プロンプトに必要な情報（対象スキル・リポジトリ名・操作）が含まれるため、確認なしで実行する

## 動作環境

- **Copilot on Windows** または **Claude Code**
- git はインストール・認証設定済み（SSH鍵 or credential manager）
- シェルは PowerShell または cmd を想定。bashコマンドは使わない

-----

## アーキテクチャ

```
ローカル（Windows）
─────────────────────────────────────────
  %USERPROFILE%\.copilot\skills\          ← スキルインストール先
    ├── skill-a\SKILL.md
    ├── skill-b\SKILL.md  (enabled)
    └── skill-c\SKILL.md  (disabled → メタデータ非ロード)

  %USERPROFILE%\.copilot\skill-registry.json  ← レジストリ

  %USERPROFILE%\.copilot\cache\           ← リポジトリキャッシュ（永続）
    ├── team-skills\                      ← 初回clone、以降はfetch
    └── personal\
─────────────────────────────────────────
         │ pull              │ pull + push
         ▼                   ▼
  ┌────────────────┐  ┌────────────────┐
  │ repo: team     │  │ repo: personal │
  │ (readonly)     │  │ (read/write)   │
  │ priority: 1    │  │ priority: 2    │
  └────────────────┘  └────────────────┘
```

-----

## レジストリ

パス: `%USERPROFILE%\.copilot\skill-registry.json`

```json
{
  "version": 2,
  "repositories": [
    {
      "name": "team-skills",
      "url": "https://github.com/myorg/agent-skills.git",
      "branch": "main",
      "skill_root": "skills",
      "description": "チーム共有スキル集",
      "readonly": false,
      "priority": 1
    }
  ],
  "installed_skills": [
    {
      "name": "docx-converter",
      "source_repo": "team-skills",
      "source_path": "skills/docx-converter",
      "commit_hash": "a1b2c3d",
      "installed_at": "2026-02-14T12:00:00Z",
      "enabled": true,
      "pinned_commit": null
    }
  ],
  "remote_index": {
    "team-skills": {
      "updated_at": "2026-02-15T10:00:00Z",
      "skills": [
        {"name": "docx-converter", "description": "Word文書をPDFに変換する..."},
        {"name": "image-resizer", "description": "画像をリサイズする..."}
      ]
    }
  },
  "profiles": {
    "default": ["*"],
    "frontend": ["react-guide", "css-linter", "storybook"],
    "backend": ["api-guide", "db-migration", "auth"]
  },
  "active_profile": null
}
```

### フィールド説明

**repositories[].priority** (整数、デフォルト: 100):
- 値が小さいほど優先度が高い
- 同名スキルの競合時、サブエージェント経由（非対話）では優先度の高いリポジトリを自動採用する
- ユーザー直接呼び出しでは対話的に選択を求める

**installed_skills[].enabled** (真偽値、デフォルト: true):
- false のスキルは `discover_skills.py` によるメタデータ収集から除外される
- ディスク上にはスキルが残るため、再有効化は即座に完了する

**installed_skills[].pinned_commit** (文字列 or null、デフォルト: null):
- null の場合、pull 時に常に最新（HEAD）を取得する
- コミットハッシュが設定されている場合、pull 時にそのコミットを checkout して取得する
- `pin` 操作で現在の commit_hash に固定、`unpin` で解除
- `lock` で全スキルを一括 pin、`unlock` で全スキルを一括 unpin

**remote_index** (オブジェクト):
- リポジトリ名 → スキル一覧のキャッシュ。`search` がこのインデックスを参照するため、ネットワーク不要で高速に検索できる
- `pull` 実行時に自動更新される
- `search --refresh` で明示的にリモートから更新できる
- `updated_at` で鮮度を確認可能

**profiles** (オブジェクト):
- プロファイル名 → スキル名のリスト。`"*"` は「全スキル」を意味する
- `active_profile` が null の場合、個別の enabled フラグに従う
- `active_profile` が設定されている場合、プロファイル内のスキルのみ enabled として扱う

レジストリが存在しなければ初回操作時に自動作成する。version: 1 のレジストリは自動マイグレーションする（新フィールドにデフォルト値を設定）。

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
|**search --refresh**|「最新のスキルを検索して」  |
|**enable**     |「スキルを有効化して」         |
|**disable**    |「スキルを無効化して」         |
|**pin**        |「スキルを固定して」「バージョンをpinして」|
|**unpin**      |「スキルの固定を解除して」       |
|**lock**       |「全スキルをロックして」        |
|**unlock**     |「全スキルのロックを解除して」     |
|**profile use**|「プロファイルを切り替えて」      |
|**profile create**|「プロファイルを作成して」    |
|**profile list**|「プロファイル一覧」         |
|**profile delete**|「プロファイルを削除して」    |

-----

## パス定義

すべての操作で以下のパスを使う。

```powershell
$SKILL_HOME   = "$env:USERPROFILE\.copilot\skills"
$REGISTRY     = "$env:USERPROFILE\.copilot\skill-registry.json"
$CACHE_DIR    = "$env:USERPROFILE\.copilot\cache"
```

初回は `$SKILL_HOME` と `$CACHE_DIR` ディレクトリを作成する:

```powershell
if (-not (Test-Path $SKILL_HOME)) { New-Item -ItemType Directory -Path $SKILL_HOME -Force }
if (-not (Test-Path $CACHE_DIR))  { New-Item -ItemType Directory -Path $CACHE_DIR -Force }
```

-----

## レジストリのマイグレーション

version: 1 のレジストリを読み込んだ場合、以下のマイグレーションを適用する。

```python
def migrate_registry(reg):
    """version 1 → 2 へのマイグレーション。"""
    if reg.get("version", 1) >= 2:
        return reg
    # repositories: priority フィールド追加
    for repo in reg.get("repositories", []):
        repo.setdefault("priority", 100)
    # installed_skills: enabled, pinned_commit フィールド追加
    for skill in reg.get("installed_skills", []):
        skill.setdefault("enabled", True)
        skill.setdefault("pinned_commit", None)
    # profiles セクション追加
    reg.setdefault("profiles", {"default": ["*"]})
    reg.setdefault("active_profile", None)
    # remote_index セクション追加
    reg.setdefault("remote_index", {})
    reg["version"] = 2
    return reg
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
            reg = json.load(f)
        return migrate_registry(reg)
    return {"version": 2, "repositories": [], "installed_skills": [],
            "remote_index": {},
            "profiles": {"default": ["*"]}, "active_profile": None}

def save_registry(reg):
    os.makedirs(os.path.dirname(registry_path), exist_ok=True)
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)

def add_repo(name, url, branch="main", skill_root="skills", description="", readonly=False, priority=100):
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
        "readonly": readonly,
        "priority": priority,
    })
    save_registry(reg)
    print(f"✅ リポジトリ '{name}' を登録しました（priority: {priority}）")
```

-----

## pull

### 処理フロー

```python
import subprocess, shutil, os, re, json, glob
from datetime import datetime

cache_dir = os.path.join(os.environ["USERPROFILE"], ".copilot", "cache")
skill_home = os.path.join(os.environ["USERPROFILE"], ".copilot", "skills")

def clone_or_fetch(repo):
    """
    キャッシュディレクトリにリポジトリを取得する。
    初回: git clone --depth 1
    2回目以降: git fetch + git reset --hard（高速）
    キャッシュが破損している場合: 削除して再clone
    """
    repo_cache = os.path.join(cache_dir, repo["name"])
    os.makedirs(cache_dir, exist_ok=True)

    if os.path.isdir(os.path.join(repo_cache, ".git")):
        # キャッシュあり → fetch で更新（高速）
        try:
            subprocess.run(
                ["git", "fetch", "origin", repo["branch"]],
                cwd=repo_cache, check=True,
                capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "reset", "--hard", f"origin/{repo['branch']}"],
                cwd=repo_cache, check=True,
                capture_output=True, text=True,
            )
            return repo_cache
        except subprocess.CalledProcessError:
            # キャッシュ破損 → 削除して再clone
            shutil.rmtree(repo_cache, ignore_errors=True)

    # 初回 or キャッシュ破損 → clone
    subprocess.run([
        "git", "clone", "--depth", "1",
        "--branch", repo["branch"],
        repo["url"], repo_cache
    ], check=True)
    return repo_cache


def update_remote_index(reg, repo_name, repo_cache, skill_root):
    """リモートインデックスを更新する。pull / search --refresh 時に呼ばれる。"""
    remote_index = reg.setdefault("remote_index", {})
    root = os.path.join(repo_cache, skill_root)
    if not os.path.isdir(root):
        return

    skills_list = []
    for entry in sorted(os.listdir(root)):
        skill_md = os.path.join(root, entry, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue
        with open(skill_md, encoding="utf-8") as f:
            content = f.read()
        desc = ""
        fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if fm_match:
            for line in fm_match.group(1).splitlines():
                if line.startswith("description:"):
                    desc = line[len("description:"):].strip()
                    break
        skills_list.append({"name": entry, "description": desc[:200]})

    remote_index[repo_name] = {
        "updated_at": datetime.now().isoformat(),
        "skills": skills_list,
    }


def pull_skills(repo_name=None, skill_name=None, interactive=True):
    """
    repo_name=None → 全リポジトリから取得
    skill_name=None → リポジトリ内の全スキルを取得
    interactive=True → ユーザー直接呼び出し（競合時に確認）
    interactive=False → サブエージェント経由（自動解決）
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
        repo_cache = clone_or_fetch(repo)

        # リモートインデックスを更新
        update_remote_index(reg, repo["name"], repo_cache, repo["skill_root"])

        root = os.path.join(repo_cache, repo["skill_root"])
        if not os.path.isdir(root):
            continue

        for entry in os.listdir(root):
            skill_md = os.path.join(root, entry, "SKILL.md")
            if not os.path.isfile(skill_md):
                continue
            if skill_name and entry != skill_name:
                continue

            # SKILL.md から description を取得
            with open(skill_md, encoding="utf-8") as f:
                content = f.read()
            desc = ""
            fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
            if fm_match:
                for line in fm_match.group(1).splitlines():
                    if line.startswith("description:"):
                        desc = line[len("description:"):].strip()
                        break

            # コミット日時を取得
            result = subprocess.run(
                ["git", "log", "-1", "--format=%aI", "--",
                 os.path.join(repo["skill_root"], entry).replace("\\", "/")],
                cwd=repo_cache, capture_output=True, text=True
            )
            commit_date = result.stdout.strip() or "1970-01-01T00:00:00+00:00"

            commit_hash = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=repo_cache, capture_output=True, text=True
            ).stdout.strip()

            candidates.setdefault(entry, []).append({
                "repo_name": repo["name"],
                "repo_priority": repo.get("priority", 100),
                "source_path": os.path.join(repo["skill_root"], entry),
                "full_path": os.path.join(root, entry),
                "commit_date": commit_date,
                "commit_hash": commit_hash,
                "description": desc[:80],
            })

    # ---- 競合解決 ----
    installed = []
    conflicts = []

    for sname, sources in candidates.items():
        winner = sources[0]

        if len(sources) > 1:
            if interactive:
                # ユーザー直接呼び出し → 対話的に選択
                print(f"\n⚠️ 競合: '{sname}' が複数リポジトリに存在します")
                for i, s in enumerate(sources, 1):
                    short_desc = s["description"] or "(説明なし)"
                    print(f"   {i}. {s['repo_name']:20s}  ({s['commit_date'][:10]})  {short_desc}")
                print(f"   どちらをインストールしますか？ (1-{len(sources)})")

                # ユーザーの選択を待つ（Claudeが対話的に処理する）
                # 選択されたインデックスを choice に格納
                # choice = <ユーザーの選択 (1-based)>
                # winner = sources[choice - 1]

                # ※ 実際にはClaude が上記メッセージを出力後、
                #   ユーザーの回答に基づいて winner を決定する
                winner = sources[0]  # プレースホルダー: Claudeが対話で決定
            else:
                # サブエージェント経由 → リポジトリ優先度で自動解決
                sources.sort(key=lambda s: s["repo_priority"])
                winner = sources[0]

            conflicts.append({
                "skill": sname,
                "adopted": winner["repo_name"],
                "rejected": [s["repo_name"] for s in sources if s != winner],
            })

        # ---- pinned_commit 対応 ----
        existing_skill = next(
            (s for s in reg.get("installed_skills", []) if s["name"] == sname),
            None,
        )
        pinned = existing_skill.get("pinned_commit") if existing_skill else None

        if pinned:
            # pin されている場合: 指定コミットを checkout してからコピー
            repo_cache = os.path.join(cache_dir, winner["repo_name"])
            try:
                subprocess.run(
                    ["git", "fetch", "--depth", "1", "origin", pinned],
                    cwd=repo_cache, check=True,
                    capture_output=True, text=True,
                )
                subprocess.run(
                    ["git", "checkout", pinned],
                    cwd=repo_cache, check=True,
                    capture_output=True, text=True,
                )
                # full_path をピン後のパスで上書き
                winner["full_path"] = os.path.join(
                    repo_cache, winner["source_path"]
                )
                winner["commit_hash"] = pinned[:7]
                print(f"   📌 {sname}: pinned commit {pinned[:7]} を使用")
            except subprocess.CalledProcessError:
                print(f"   ⚠️ {sname}: pinned commit {pinned[:7]} の取得に失敗。最新版を使用します")
                pinned = None

        dest = os.path.join(skill_home, sname)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(winner["full_path"], dest)

        # 既存の enabled / pinned_commit 状態を保持
        enabled = existing_skill.get("enabled", True) if existing_skill else True

        installed.append({
            "name": sname,
            "source_repo": winner["repo_name"],
            "source_path": winner["source_path"],
            "commit_hash": winner["commit_hash"],
            "installed_at": datetime.now().isoformat(),
            "enabled": enabled,
            "pinned_commit": pinned,
        })

    # レジストリ更新
    existing = {s["name"]: s for s in reg.get("installed_skills", [])}
    for s in installed:
        existing[s["name"]] = s
    reg["installed_skills"] = list(existing.values())
    save_registry(reg)

    # 結果レポート
    print(f"\n📦 pull 完了")
    print(f"   新規/更新: {len(installed)} 件")
    if conflicts:
        print(f"   競合解決:  {len(conflicts)} 件")
        for c in conflicts:
            print(f"     {c['skill']}: {c['adopted']} を採用（{', '.join(c['rejected'])} を不採用）")
    for s in installed:
        pin_mark = f" 📌{s['pinned_commit'][:7]}" if s.get("pinned_commit") else ""
        status = "✅" if s["enabled"] else "⏸️"
        print(f"   {status} {s['name']} ← {s['source_repo']} ({s['commit_hash']}){pin_mark}")
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

    if repo.get("readonly", False):
        print(f"❌ リポジトリ '{repo_name}' は readonly です。push できません")
        return

    skill_md = os.path.join(skill_path, "SKILL.md")
    if not os.path.isfile(skill_md):
        print(f"❌ SKILL.md が見つかりません: {skill_path}")
        return

    skill_name = os.path.basename(skill_path.rstrip("\\/"))

    # push 用は一時ディレクトリを使用（キャッシュとは別）
    temp_work = os.path.join(os.environ["TEMP"], "agent-skill-push")
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
    active_profile = reg.get("active_profile")
    profiles = reg.get("profiles", {})

    print(f"📂 スキル一覧 ({skill_home})")
    if active_profile:
        print(f"   アクティブプロファイル: {active_profile}")
    print()

    if not os.path.isdir(skill_home):
        print("   (スキルなし)")
        return

    for entry in sorted(os.listdir(skill_home)):
        if not os.path.isfile(os.path.join(skill_home, entry, "SKILL.md")):
            continue
        info = registry_map.get(entry, {})
        repo = info.get("source_repo", "local")
        hash_ = info.get("commit_hash", "-")
        enabled = is_skill_enabled(entry, reg)
        pinned = info.get("pinned_commit")
        status = "✅" if enabled else "⏸️"
        pin_mark = f" 📌{pinned[:7]}" if pinned else ""
        print(f"   {status} {entry:30s}  repo: {repo:20s}  commit: {hash_}{pin_mark}")


def is_skill_enabled(skill_name, reg):
    """スキルが有効かどうかを判定する。プロファイルとenabledフラグの両方を考慮。"""
    active_profile = reg.get("active_profile")
    profiles = reg.get("profiles", {})

    if active_profile and active_profile in profiles:
        profile_skills = profiles[active_profile]
        if "*" not in profile_skills and skill_name not in profile_skills:
            return False

    # 個別の enabled フラグ
    skill_info = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if skill_info and not skill_info.get("enabled", True):
        return False

    return True
```

-----

## search

デフォルトではレジストリ内の `remote_index` を検索する（ネットワーク不要、即座に結果を返す）。
`--refresh` 指定時はリモートから最新情報を取得してインデックスを更新してから検索する。
インデックスが空の場合（初回）は自動的に `--refresh` と同様の動作をする。

```python
def search_skills(repo_name=None, keyword=None, refresh=False):
    reg = load_registry()
    repos = reg["repositories"]
    if repo_name:
        repos = [r for r in repos if r["name"] == repo_name]

    remote_index = reg.get("remote_index", {})

    # インデックスが空 or --refresh → リモートからインデックスを更新
    needs_refresh = refresh or not any(
        repo["name"] in remote_index for repo in repos
    )

    if needs_refresh:
        print("🔄 リモートからインデックスを更新中...")
        for repo in repos:
            repo_cache = clone_or_fetch(repo)
            update_remote_index(reg, repo["name"], repo_cache, repo["skill_root"])
        save_registry(reg)
        remote_index = reg.get("remote_index", {})

    # インデックスから検索
    for repo in repos:
        index_entry = remote_index.get(repo["name"])
        if not index_entry:
            continue

        print(f"\n🔍 {repo['name']} ({repo['url']})")
        updated = index_entry.get("updated_at", "不明")[:10]
        print(f"   (インデックス更新日: {updated})")

        found = False
        for skill in index_entry.get("skills", []):
            name = skill["name"]
            desc = skill.get("description", "")

            if keyword and keyword.lower() not in name.lower() and keyword.lower() not in desc.lower():
                continue

            found = True
            short_desc = desc[:80] + "..." if len(desc) > 80 else desc
            print(f"   {name:30s}  {short_desc}")

        if not found:
            print("   (該当なし)")
```

-----

## enable / disable

スキルの有効・無効を切り替える。無効化されたスキルはディスク上に残るが、`discover_skills.py` のメタデータ収集から除外される（コンテキストウィンドウを節約）。

```python
def enable_skill(skill_name):
    reg = load_registry()
    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        print(f"❌ スキル '{skill_name}' がインストールされていません")
        return
    if skill.get("enabled", True):
        print(f"ℹ️ スキル '{skill_name}' は既に有効です")
        return
    skill["enabled"] = True
    save_registry(reg)
    print(f"✅ スキル '{skill_name}' を有効化しました")


def disable_skill(skill_name):
    reg = load_registry()
    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        print(f"❌ スキル '{skill_name}' がインストールされていません")
        return
    if not skill.get("enabled", True):
        print(f"ℹ️ スキル '{skill_name}' は既に無効です")
        return
    skill["enabled"] = False
    save_registry(reg)
    print(f"⏸️ スキル '{skill_name}' を無効化しました")
```

-----

## pin / unpin

スキルを特定のコミットハッシュに固定する。pin されたスキルは pull 時にそのコミットの内容を取得し、新しいバージョンには更新されない。

```python
def pin_skill(skill_name, commit=None):
    """
    commit=None → 現在インストール済みの commit_hash に固定
    commit=指定 → 指定コミットに固定
    """
    reg = load_registry()
    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        print(f"❌ スキル '{skill_name}' がインストールされていません")
        return

    target = commit or skill.get("commit_hash")
    if not target:
        print(f"❌ コミットハッシュが不明です。先に pull してください")
        return

    skill["pinned_commit"] = target
    save_registry(reg)
    print(f"📌 スキル '{skill_name}' を {target[:7]} に固定しました")


def unpin_skill(skill_name):
    reg = load_registry()
    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        print(f"❌ スキル '{skill_name}' がインストールされていません")
        return
    if not skill.get("pinned_commit"):
        print(f"ℹ️ スキル '{skill_name}' は固定されていません")
        return

    skill["pinned_commit"] = None
    save_registry(reg)
    print(f"🔓 スキル '{skill_name}' の固定を解除しました（次回 pull で最新版を取得します）")
```

-----

## lock / unlock

全インストール済みスキルのバージョンを一括で固定・解除する。チームで同じバージョンのスキルセットを共有するときに使う。

```python
def lock_all():
    """全スキルを現在の commit_hash に一括固定する。"""
    reg = load_registry()
    skills = reg.get("installed_skills", [])
    locked = 0

    for skill in skills:
        hash_ = skill.get("commit_hash")
        if hash_ and not skill.get("pinned_commit"):
            skill["pinned_commit"] = hash_
            locked += 1

    save_registry(reg)
    print(f"🔒 lock 完了: {locked} 件のスキルを固定しました")
    for skill in skills:
        pin = skill.get("pinned_commit")
        if pin:
            print(f"   📌 {skill['name']:30s}  {pin[:7]}")


def unlock_all():
    """全スキルの固定を一括解除する。"""
    reg = load_registry()
    skills = reg.get("installed_skills", [])
    unlocked = 0

    for skill in skills:
        if skill.get("pinned_commit"):
            skill["pinned_commit"] = None
            unlocked += 1

    save_registry(reg)
    print(f"🔓 unlock 完了: {unlocked} 件のスキルの固定を解除しました")
```

-----

## profile

プロファイルはスキルの有効・無効を一括で切り替えるショートカット。プロファイルをアクティブにすると、そのプロファイルに含まれるスキルのみがコンテキストにロードされる。

### profile create

```python
def profile_create(profile_name, skill_names):
    """
    profile_name: プロファイル名
    skill_names: スキル名のリスト（"*" で全スキル）
    """
    reg = load_registry()
    profiles = reg.setdefault("profiles", {})

    if profile_name in profiles:
        print(f"⚠️ プロファイル '{profile_name}' を上書きします")

    profiles[profile_name] = skill_names
    save_registry(reg)
    print(f"✅ プロファイル '{profile_name}' を作成しました: {', '.join(skill_names)}")
```

### profile use

```python
def profile_use(profile_name):
    """プロファイルをアクティブにする。None を指定すると個別 enabled に戻る。"""
    reg = load_registry()
    profiles = reg.get("profiles", {})

    if profile_name is not None and profile_name not in profiles:
        print(f"❌ プロファイル '{profile_name}' が見つかりません")
        print(f"   利用可能: {', '.join(profiles.keys())}")
        return

    reg["active_profile"] = profile_name
    save_registry(reg)

    if profile_name is None:
        print("✅ プロファイルを解除しました（個別の enabled 設定に従います）")
    else:
        skills = profiles[profile_name]
        label = "全スキル" if "*" in skills else ", ".join(skills)
        print(f"✅ プロファイル '{profile_name}' をアクティブにしました: {label}")
```

### profile list

```python
def profile_list():
    reg = load_registry()
    profiles = reg.get("profiles", {})
    active = reg.get("active_profile")

    if not profiles:
        print("   (プロファイルなし)")
        return

    print("📋 プロファイル一覧\n")
    for name, skills in profiles.items():
        marker = " ◀ active" if name == active else ""
        label = "全スキル" if "*" in skills else ", ".join(skills)
        print(f"   {name:20s}  [{label}]{marker}")
```

### profile delete

```python
def profile_delete(profile_name):
    reg = load_registry()
    profiles = reg.get("profiles", {})

    if profile_name not in profiles:
        print(f"❌ プロファイル '{profile_name}' が見つかりません")
        return

    if profile_name == "default":
        print(f"❌ 'default' プロファイルは削除できません")
        return

    if reg.get("active_profile") == profile_name:
        reg["active_profile"] = None

    del profiles[profile_name]
    save_registry(reg)
    print(f"✅ プロファイル '{profile_name}' を削除しました")
```

-----

## エラーハンドリング

|エラー               |対処                         |
|------------------|---------------------------|
|`git ls-remote` 失敗|URL・認証を確認するよう案内            |
|clone 失敗          |ブランチ名を `git ls-remote` で確認 |
|fetch 失敗（キャッシュ破損）|キャッシュを削除して再clone          |
|push to readonly  |readonlyリポジトリへのpush拒否を通知。別リポジトリを提案する|
|push rejected     |`git pull --rebase` 後に再push|
|SKILL.md なし       |スキルフォルダの構成確認を案内            |
|レジストリ破損           |削除して再作成するか、リポジトリから再pull    |
|ネットワークエラー         |ネットワーク接続を確認するよう案内          |

-----

## 使用例

### 初回セットアップ

```
ユーザー: 「https://github.com/myorg/skills.git をスキルリポジトリに登録して」

Copilot:
  1. git ls-remote で接続確認
  2. レジストリ作成、リポジトリ追加（readonlyにするか確認、priorityを確認）
  3. 「登録しました。pullしますか？」
```

### readonlyリポジトリの登録

```
ユーザー: 「https://github.com/otherteam/skills.git を参照専用で登録して」

Copilot:
  1. git ls-remote で接続確認
  2. readonly: true でレジストリに追加
  3. 「readonlyで登録しました。pullのみ可能です」
```

### pull（キャッシュ活用）

```
ユーザー: 「スキルを全部同期して」

Copilot:
  1. 全リポジトリを cache からfetch（初回のみclone）
  2. 各リポジトリのスキルを走査
  3. 同名競合があればユーザーに確認
  4. %USERPROFILE%\.copilot\skills\ にコピー、レジストリ更新
  5. 結果レポート（有効/無効状態も表示）
```

### push

```
ユーザー: 「今作ったスキルを team-skills にpushして」

Copilot:
  1. レジストリから team-skills の情報を取得
  2. SKILL.md の存在確認
  3. clone → ブランチ作成 → コピー → commit → push
  4. コミットハッシュとブランチ名を報告
```

### スキルの無効化

```
ユーザー: 「legacy-tool スキルを無効化して」

Copilot:
  1. レジストリの enabled を false に変更
  2. 「legacy-tool を無効化しました。再有効化は 'スキルを有効化して' で可能です」
```

### 検索（オフライン）

```
ユーザー: 「converter で検索して」

Copilot:
  1. レジストリの remote_index から keyword=converter で検索（ネットワーク不要）
  2. 結果を表示（インデックス更新日も表示）
```

### 検索（最新を取得）

```
ユーザー: 「最新のスキルを検索して」

Copilot:
  1. 全リポジトリから fetch してインデックスを更新
  2. 更新後のインデックスから検索結果を表示
```

### スキルのバージョン固定

```
ユーザー: 「docx-converter を今のバージョンに固定して」

Copilot:
  1. 現在の commit_hash を pinned_commit に設定
  2. 「docx-converter を a1b2c3d に固定しました」
```

### 全スキルのロック

```
ユーザー: 「全スキルをロックして」

Copilot:
  1. 全 installed_skills の commit_hash を pinned_commit に設定
  2. ロックされたスキル一覧を表示
```

### プロファイル切り替え

```
ユーザー: 「フロントエンド開発用のプロファイルに切り替えて」

Copilot:
  1. frontend プロファイルをアクティブに設定
  2. 「frontend プロファイルをアクティブにしました: react-guide, css-linter, storybook」
```
