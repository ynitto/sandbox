#!/usr/bin/env python3
"""codd-agent が使う外部のミドルウェアを端末に入れる（端末ごとに 1 回と、更新するとき）。

    python3 tools/codd-agent/install.py              # 足りないものを入れる
    python3 tools/codd-agent/install.py --upgrade    # 入っているものも最新にする
    python3 tools/codd-agent/install.py --agent kiro # Kiro IDE / CLI 向けのスキルだけ配置

入れるもの:
  graphify   参照先を探すときの知識グラフ（任意。無ければ文字列検索だけで動く）。uv → pipx → pip の順で入れる
  caveman / graphify の外部スキル   既定は Kiro・Copilot の両方。エージェント CLI は不要。
      caveman は公式 GitHub、graphify は公式パッケージから配置する。スキルは内包しない。

確かめるだけのもの（入れ方は表示する）:
  git        必須

codd そのものは端末に入れない。リポジトリごとに `init.py` で置く（リポジトリだけで動くように）。

    python3 tools/codd-agent/init.py <リポジトリ> --side impl --ref docs=../my-design

以前の使い方（`install.py <リポジトリ> --side … --ref …`）で呼ばれたときは、そのまま init.py に渡す。
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib import request as urllib_request

HERE = Path(__file__).resolve().parent
TIMEOUT = 600
AGENT_KINDS = ("kiro", "copilot")


CAVEMAN_REPO = "JuliusBrussee/caveman"


def setup_caveman(agent_type: str, force: bool = False) -> bool:
    """公式配布元から取得し、IDE が読む保存先へ配置する。CLI / npx は不要。"""
    if agent_type not in AGENT_KINDS:
        raise ValueError(f"未対応の導入先: {agent_type}")
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    dest = home / f".{agent_type}" / "skills" / "caveman"
    if (dest / "SKILL.md").is_file() and not force:
        print(f"   ✓ caveman は導入済み: {dest}")
        return True
    url = f"https://codeload.github.com/{CAVEMAN_REPO}/zip/refs/heads/main"
    print(f"   caveman を公式配布元から取得: {url}")
    try:
        request = urllib_request.Request(url, headers={"User-Agent": "agent-skills-installer"})
        with urllib_request.urlopen(request, timeout=60) as response:
            data = response.read()
        with zipfile.ZipFile(io.BytesIO(data)) as archive, tempfile.TemporaryDirectory() as temp:
            root = archive.namelist()[0].split("/", 1)[0]
            prefix = f"{root}/skills/caveman/"
            staged = Path(temp) / "caveman"
            for entry in archive.infolist():
                if not entry.filename.startswith(prefix) or entry.is_dir():
                    continue
                rel = entry.filename[len(prefix):]
                if (not rel or "\\" in rel or Path(rel).drive or PurePosixPath(rel).is_absolute()
                        or ".." in PurePosixPath(rel).parts):
                    raise ValueError("スキルのアーカイブに不正なパスがあります")
                file = staged / rel
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(archive.read(entry))
            if not (staged / "SKILL.md").is_file():
                raise ValueError("公式アーカイブに caveman/SKILL.md がありません")
            license_path = f"{root}/LICENSE"
            if license_path in archive.namelist():
                (staged / "LICENSE").write_bytes(archive.read(license_path))
            shutil.copytree(staged, dest, dirs_exist_ok=True)
        print(f"   ✓ caveman を配置: {dest}")
        return True
    except (OSError, ValueError, IndexError, zipfile.BadZipFile) as exc:
        print(f"   ✗ caveman の取得・配置に失敗しました: {exc}")
        return False

# (名前, 入っているかを見るコマンド, 入れるコマンドの候補, 最新にするコマンドの候補)
GRAPHIFY = ("graphify", ["graphify", "--version"],
            [["uv", "tool", "install", "graphifyy"], ["pipx", "install", "graphifyy"],
             [sys.executable, "-m", "pip", "install", "--user", "graphifyy"]],
            [["uv", "tool", "upgrade", "graphifyy"], ["pipx", "upgrade", "graphifyy"],
             [sys.executable, "-m", "pip", "install", "--user", "-U", "graphifyy"]])
CHECK_ONLY = (("git", ["git"], "https://git-scm.com/downloads から入れる（必須）"),)


def version(cmd: list[str]) -> str | None:
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        r = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (r.stdout.strip() or r.stderr.strip() or "入っている").splitlines()[0] if r.returncode == 0 else None


def run_first(candidates: list[list[str]]) -> str | None:
    """使える道具で順に試し、最初に通ったものの名前を返す。"""
    for cmd in candidates:
        exe = shutil.which(cmd[0])
        if not exe:
            continue
        print(f"  {' '.join(cmd)}")
        try:
            r = subprocess.run([exe, *cmd[1:]], timeout=TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return Path(cmd[0]).name
    return None


def install_middleware(upgrade: bool = False, agents=AGENT_KINDS, skills: bool = True) -> int:
    name, probe, installs, upgrades = GRAPHIFY
    current = version(probe)
    failed = 0
    if current and not upgrade:
        print(f"✓ {name}: {current}")
    else:
        print(f"{name} を{'最新にします' if current else '入れます'}")
        if run_first(upgrades if current else installs):
            print(f"✓ {name}: {version(probe) or '入れた（PATH を確かめてください）'}")
        else:
            failed += 1
            print(f"✗ {name} を入れられませんでした（uv・pipx・pip のどれかが要ります）。無くても文字列検索だけで動きます")
    if skills:
        exe = shutil.which("graphify")
        for agent in dict.fromkeys(agents):
            if not setup_caveman(agent, force=upgrade):
                failed += 1
            if not exe:
                print(f"✗ {agent} 向け graphify スキルを登録できません（graphify 本体がありません）")
                failed += 1
                continue
            try:
                result = subprocess.run([exe, "install", "--platform", agent], timeout=TIMEOUT)
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f"✗ {agent} 向け graphify スキルの登録に失敗しました: {exc}")
                failed += 1
                continue
            if result.returncode:
                print(f"✗ {agent} 向け graphify スキルの登録に失敗しました（{result.returncode}）")
                failed += 1
            else:
                print(f"✓ {agent} 向け graphify スキルを登録しました")
    for name, probe, how in CHECK_ONLY:
        found = shutil.which(probe[0])
        print(f"✓ {name}: {found}" if found else f"- {name}: ありません。{how}")
    print("codd はリポジトリごとに置きます: python3 tools/codd-agent/init.py <リポジトリ> --side … --ref …")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(description="codd-agent の graphify 本体と caveman・graphify 外部スキルを導入する")
    p.add_argument("--upgrade", action="store_true", help="入っているものも最新にする")
    p.add_argument("--agent", action="append", choices=AGENT_KINDS,
                   help="スキルの導入先（繰り返し可。既定は Kiro と Copilot の両方。CLI 不要）")
    p.add_argument("--no-skills", action="store_true", help="外部スキルを配置せず、graphify 本体だけ導入する")
    args, legacy = p.parse_known_args(argv)
    if legacy:
        # 以前の使い方（リポジトリを渡す）。リポジトリへ置くのは init.py の仕事。
        print("リポジトリへ置くのは init.py に分けました（install.py は外部のミドルウェアを入れます）。init.py に渡します。")
        sys.path.insert(0, str(HERE))
        import init as repo_init
        return repo_init.main(argv)
    return install_middleware(args.upgrade, agents=args.agent or AGENT_KINDS, skills=not args.no_skills)


if __name__ == "__main__":
    sys.exit(main())
