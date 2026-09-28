"""install_laya — laya（Jev 互換の判断モデル）を CPU だけで動くように入れる。Windows / macOS / Linux 共通。

やること（すべて `<~/.agents>/laya` の下に閉じる。消すときはこのフォルダを消すだけ）:

1. 専用の仮想環境（venv）を作る
2. CPU 版の PyTorch を入れる（Linux / Windows は CPU 専用の配布元から。GPU 版の数 GB を落とさない。
   macOS の通常版はもともと GPU（CUDA）を含まない）
3. laya 本体と、推論に要る部品だけを入れる（laya のサーバ用の追加部品 fastapi / uvicorn は入れず、
   標準ライブラリだけのサーバ `laya_server.py` を置く）
4. 入った部品のライセンスを一覧にし、コピーレフト（GPL / LGPL / AGPL）が紛れていたら止まる
5. 多言語版のモデル（日本語を読める。約 650 MB）だけを落とし、日本語の問いを 1 つ解いて確かめる
6. 起動用のスクリプトを置き、agent-herd があれば選択・振り分けの第 1 段を laya に向ける

使い方:
  python install_laya.py                 # 既定の場所へ入れる
  python install_laya.py --dry-run       # 何をするかだけ出す
  python install_laya.py --home <dir> --port 8000 --no-configure

標準ライブラリだけで動く（Python 3.10 以上）。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import venv
from pathlib import Path

LAYA_VERSION = "0.3.21"
LAYA_REPO = "convaiinnovations/laya"
MODEL = "multilingual"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
# laya が推論に要る部品（laya 自身の依存から、サーバ用の追加部品を除いたもの）。
RUNTIME_PACKAGES = ("transformers>=4.48.0,<5", "safetensors>=0.4.0", "huggingface_hub>=0.20.0",
                    "numpy>=1.20.0")
MIN_PYTHON = (3, 10)
DEFAULT_PORT = 8000
HERE = Path(__file__).resolve().parent
SERVER_SCRIPT = HERE / "laya_server.py"

# ライセンスの区分。Apache / MIT を基本に、同じ寛容型の BSD などは「許容」、コピーレフトは止める。
PREFERRED = ("apache", "mit")
PERMISSIVE = ("bsd", "0bsd", "bsl", "boost", "isc", "cnri", "psf", "python software foundation", "unlicense", "zlib", "hpnd",
              "3-clause", "2-clause", "cc0")
WEAK_COPYLEFT = ("mpl", "mozilla public")
COPYLEFT = ("gpl", "general public license", "agpl", "lgpl", "eupl", "sspl")


def agents_home() -> Path:
    override = os.environ.get("AGENT_PROJECT_AGENTS_HOME")
    return Path(override).expanduser() if override else Path.home() / ".agents"


def default_home() -> Path:
    return agents_home() / "laya"


def venv_python(home: Path, system: "str | None" = None) -> Path:
    system = system or platform.system()
    if system == "Windows":
        return home / "venv" / "Scripts" / "python.exe"
    return home / "venv" / "bin" / "python"


def torch_index(system: "str | None" = None, override: "str | None" = None) -> "str | None":
    """CPU 版 PyTorch の配布元。macOS は通常の PyPI（CUDA を含まない）。"""
    if override:
        return override
    system = system or platform.system()
    return None if system == "Darwin" else TORCH_CPU_INDEX


def pip_commands(python: Path, *, system: "str | None" = None,
                 torch_index_url: "str | None" = None) -> "list[list[str]]":
    base = [str(python), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check"]
    index = torch_index(system, torch_index_url)
    torch_cmd = base + ["torch>=2.2"] + (["--index-url", index] if index else [])
    return [
        base + ["--upgrade", "pip"],
        torch_cmd,
        base + list(RUNTIME_PACKAGES),
        base + ["--no-deps", f"laya=={LAYA_VERSION}"],
    ]


def _has(low: str, words, *, whole: bool = False) -> bool:
    # 前は英字でないこと（"0BSD" の BSD は拾い、"permit" の mit は拾わない）。
    tail = r"(?![a-z])" if whole else ""
    return any(re.search(rf"(?<![a-z]){re.escape(w)}{tail}", low) for w in words)


def _classify_atom(text: str) -> str:
    low = text.lower()
    if _has(low, COPYLEFT):
        return "copyleft"
    if _has(low, WEAK_COPYLEFT):
        return "weak-copyleft"
    if _has(low, PREFERRED, whole=True):
        return "preferred"
    if _has(low, PERMISSIVE):
        return "permissive"
    return "unknown"


def classify_license(text: str) -> str:
    """ライセンス表記 → preferred / permissive / unknown / weak-copyleft / copyleft。

    `A OR B` は選べるので緩い方、`A AND B`（と分類子の並び）は全部に従うので厳しい方を採る。"""
    raw = re.sub(r"[()]", " ", text or "").strip()
    if not raw:
        return "unknown"
    options = [part for part in re.split(r"\s+or\s+", raw, flags=re.I) if part.strip()]
    ranks = []
    for option in options:
        atoms = [a for a in re.split(r"\s+and\s+|\s*;\s*", option, flags=re.I) if a.strip()]
        ranks.append(max((_classify_atom(a) for a in atoms), key=_RANK.index))
    return min(ranks, key=_RANK.index)


_RANK = ("preferred", "permissive", "unknown", "weak-copyleft", "copyleft")

# 仮想環境の中で走らせ、入っている部品とライセンス表記を JSON で返す。
_LICENSE_PROBE = r"""
import json
from importlib import metadata
rows = []
for dist in metadata.distributions():
    meta = dist.metadata
    name = meta.get("Name") or ""
    if not name or name.lower() in ("pip", "setuptools", "wheel"):
        continue
    expr = meta.get("License-Expression") or ""
    lic = (meta.get("License") or "").strip().splitlines()[0:1]
    classifiers = [c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or [])
                   if c.startswith("License ::")]
    rows.append({"name": name, "version": dist.version, "expression": expr,
                 "license": lic[0] if lic else "", "classifiers": classifiers})
print(json.dumps(sorted(rows, key=lambda r: r["name"].lower())))
"""


def license_of(row: dict) -> str:
    """表記の優先順: License-Expression（SPDX）→ 分類子 → License 欄（短いときだけ）。"""
    if row.get("expression"):
        return row["expression"]
    classifiers = [c for c in row.get("classifiers") or [] if c.lower() != "osi approved"]
    if classifiers:
        return " AND ".join(classifiers)
    text = row.get("license") or ""
    return text if len(text) <= 80 else ""


def license_report(rows: "list[dict]") -> dict:
    report = {kind: [] for kind in _RANK}
    for row in rows:
        text = license_of(row)
        report[classify_license(text)].append({"name": row["name"], "version": row["version"],
                                               "license": text or "(表記なし)"})
    return report


def dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def launcher_text(home: Path, port: int, system: "str | None" = None) -> "tuple[str, str]":
    """起動スクリプトの (ファイル名, 中身)。"""
    system = system or platform.system()
    python = venv_python(home, system)
    server = home / "laya_server.py"
    if system == "Windows":
        return ("laya-serve.cmd",
                "@echo off\r\n"
                f'set "HF_HOME={home / "hf"}"\r\n'
                f'"{python}" "{server}" --port {port} %*\r\n')
    return ("laya-serve.sh",
            "#!/bin/sh\n"
            f'export HF_HOME="{home / "hf"}"\n'
            f'exec "{python}" "{server}" --port {port} "$@"\n')


class Installer:
    def __init__(self, home: Path, *, port: int = DEFAULT_PORT, torch_index_url: "str | None" = None,
                 configure: bool = True, dry_run: bool = False, out=None):
        self.home = home
        self.port = port
        self.torch_index_url = torch_index_url
        self.configure = configure
        self.dry_run = dry_run
        self.out = out or sys.stdout
        self.python = venv_python(home)

    def say(self, text: str) -> None:
        print(text, file=self.out, flush=True)

    def run(self, cmd: "list[str]", *, env: "dict | None" = None, capture: bool = False) -> str:
        self.say("  $ " + " ".join(cmd))
        if self.dry_run:
            return ""
        merged = dict(os.environ, **(env or {}))
        if capture:
            return subprocess.run(cmd, check=True, env=merged, text=True,
                                  stdout=subprocess.PIPE).stdout
        subprocess.run(cmd, check=True, env=merged)
        return ""

    @property
    def model_env(self) -> dict:
        # モデルの置き場も laya のフォルダの中へ（利用者の Hugging Face の共有キャッシュを汚さない）。
        return {"HF_HOME": str(self.home / "hf"), "HF_HUB_DISABLE_TELEMETRY": "1",
                "CUDA_VISIBLE_DEVICES": ""}

    def create_venv(self) -> None:
        self.say(f"[1/6] 仮想環境を作ります: {self.home / 'venv'}")
        if self.dry_run or self.python.exists():
            return
        self.home.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True, clear=False).create(self.home / "venv")

    def install_packages(self) -> None:
        index = torch_index(override=self.torch_index_url)
        self.say(f"[2/6] CPU 版の PyTorch と laya {LAYA_VERSION} を入れます"
                 f"（PyTorch の配布元: {index or 'PyPI'}）")
        for cmd in pip_commands(self.python, torch_index_url=self.torch_index_url):
            self.run(cmd)

    def check_licenses(self) -> dict:
        self.say("[3/6] 入った部品のライセンスを確かめます")
        if self.dry_run:
            return {}
        self.say("  （仮想環境の中の部品を読みます）")
        rows = json.loads(subprocess.run([str(self.python), "-c", _LICENSE_PROBE], check=True, text=True,
                                         stdout=subprocess.PIPE).stdout or "[]")
        report = license_report(rows)
        (self.home / "licenses.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                 encoding="utf-8")
        labels = {"preferred": "Apache / MIT", "permissive": "BSD など（寛容型）",
                  "unknown": "表記を読めない", "weak-copyleft": "MPL（弱いコピーレフト。改変しなければ義務なし）",
                  "copyleft": "コピーレフト"}
        for kind in _RANK:
            if report[kind]:
                names = ", ".join(f"{r['name']}（{r['license']}）" if kind != "preferred" else r["name"]
                                  for r in report[kind])
                self.say(f"  {labels[kind]}: {names}")
        if report["copyleft"]:
            raise SystemExit("コピーレフトの部品が入ったので止めます: "
                             + ", ".join(r["name"] for r in report["copyleft"]))
        return report

    def install_server(self) -> None:
        self.say("[4/6] サーバと起動スクリプトを置きます")
        name, text = launcher_text(self.home, self.port)
        self.say(f"  {self.home / 'laya_server.py'} / {self.home / name}")
        if self.dry_run:
            return
        shutil.copy2(SERVER_SCRIPT, self.home / "laya_server.py")
        launcher = self.home / name
        launcher.write_text(text, encoding="utf-8", newline="")
        if platform.system() != "Windows":
            launcher.chmod(0o755)

    def download_and_selftest(self) -> None:
        self.say(f"[5/6] 多言語版のモデル（約 650 MB）だけを落とし、日本語の問いを 1 つ解きます")
        fetch = ("from huggingface_hub import snapshot_download; "
                 f"snapshot_download({LAYA_REPO!r}, allow_patterns=['{MODEL}/*'])")
        self.run([str(self.python), "-c", fetch], env=self.model_env)
        self.run([str(self.python), str(self.home / "laya_server.py"), "--selftest"], env=self.model_env)

    def configure_herd(self) -> None:
        self.say("[6/6] agent-herd の第 1 段を laya に向けます")
        if not self.configure:
            self.say("  （--no-configure のため省略）")
            return
        herd = shutil.which("agent-herd")
        if not herd:
            self.say("  agent-herd が見つからないので省略しました。入っている環境で次を実行してください:\n"
                     "    agent-herd config set select.jev.backend laya")
            return
        self.run([herd, "config", "set", "select.jev.backend", "laya"])
        if self.port != DEFAULT_PORT:
            self.run([herd, "config", "set", "select.jev.endpoint",
                      f"http://127.0.0.1:{self.port}/v1/systemone"])

    def install(self) -> int:
        if sys.version_info < MIN_PYTHON:
            raise SystemExit(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 以上が要ります（いまは {platform.python_version()}）")
        self.create_venv()
        self.install_packages()
        self.check_licenses()
        self.install_server()
        self.download_and_selftest()
        self.configure_herd()
        name, _ = launcher_text(self.home, self.port)
        if not self.dry_run:
            self.say(f"使う容量: {dir_size(self.home) / 1024 / 1024:.0f} MB（{self.home}）")
        self.say(f"起動: {self.home / name}（http://127.0.0.1:{self.port}/v1/systemone）")
        return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="install_laya",
                                     description="laya を CPU だけで動くように入れる（Windows / macOS / Linux）")
    parser.add_argument("--home", type=Path, default=None, help="入れる場所（既定 ~/.agents/laya）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--torch-index-url", default=os.environ.get("LAYA_TORCH_INDEX_URL") or None,
                        help="PyTorch の配布元を差し替える（社内ミラーなど）")
    parser.add_argument("--no-configure", action="store_true", help="agent-herd の設定を書き換えない")
    parser.add_argument("--dry-run", action="store_true", help="何をするかだけ出す")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows の古いコンソールでも日本語で落ちない
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    home = (args.home or default_home()).expanduser().resolve()
    try:
        return Installer(home, port=args.port, torch_index_url=args.torch_index_url,
                         configure=not args.no_configure, dry_run=args.dry_run).install()
    except subprocess.CalledProcessError as exc:
        print(f"失敗しました（終了コード {exc.returncode}）: {' '.join(map(str, exc.cmd))[:300]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
