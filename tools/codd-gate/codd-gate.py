#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""codd-gate — ドキュメント・コード・テストの一貫性ゲート（単体で動く決定的 CLI）。

CoDD（Coherence-Driven Development, https://github.com/yohey-w/codd-dev）の設計
（Trace=接続マップ / Impact=Green・Amber・Gray 分類 / Verify=偽グリーンを許さない検証）の翻案。
依存は python3 と git のみ（pip 依存なし・LLM 不要）。単体で CI・git hook・手元の点検に使え、
kiro-autonomous とは既存フック経由でオプション連携できる（依存は一方向・本体無改造）。

  scan    … 接続マップ（doc↔code↔test のエッジ）と既存負債（壊れた参照・未文書化・未テスト）の棚卸し
  impact  … 差分（$KIRO_BASE_REV..作業ツリー）を Green / Amber / Gray / Followup に分類
  verify  … ゲート（終了コード 0/1）。Amber（同一 repo のドリフト・壊れた参照）で NG
  tasks   … ドリフト/負債を kiro-autonomous の enqueue --json / inbox 形式の修復タスクに変換
  check   … 修復タスクの verify 用の状態アサーション（エッジ存在・参照解決・鮮度）

リポジトリレジストリは設定ファイルの `repos:`（codd-gate ネイティブ。charter 書式に依存しない）。
identity は (url, path, base)＝パス＋ブランチで一意。

kiro-autonomous と連携する場合も本体は無改造で、既存の決定的フックだけで結合する（＝プラグイン）:
  - `--charter` は**連携アダプタ**: charter.md の ## repos をレジストリとして読める（二重管理の回避）。
    アダプタ専用キー `- docs:` `- tests:` `- code:` は kiro-autonomous には未知キーとして無害。
  - タスク verify / regression_cmd に `codd-gate verify --base "$KIRO_BASE_REV"` を差し込む（差分ゲート）。
  - charter acceptance に `codd-gate verify --debt --max-broken N` を置く（負債ラチェット）。
  - kiro-autonomous の `intake_cmd: 'codd-gate tasks --debt'` で watch の周期ごとに負債を自動で
    汲み上げる（id が冪等キー。手動なら `codd-gate tasks | kiro-autonomous enqueue --json` / --inbox）。

鉄則（kiro-autonomous と同じ流儀）:
  1. verify は「履歴」ではなく「現在の状態と差分」だけを見る。マップのキャッシュを信用せず毎回スキャンする。
  2. 成果の無い場所で偽判定しない。チェック対象 repo のディレクトリが解決できなければ NG（黙って PASS しない）。
  3. ブラウンフィールドの既存負債では止めない。差分ゲートは「新しく壊した分」だけを NG にし、
     既存負債は棚卸し（--debt）→ タスク化（tasks --debt）→ ラチェット（--max-*）で漸進的に返す。
  4. 修復の知能は kiro-autonomous → kiro-flow へ委譲する。本体は分類とタスク生成まで。
  5. **どのサブコマンドも単発・有界**（watch/daemon を持たない。git 呼び出しも個別にタイムアウト）。
     常駐・繰り返しは kiro-autonomous（intake_cmd / regression_cmd / acceptance）や cron・CI が持つ。
"""

import argparse
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import fcntl
except ImportError:                             # 非 POSIX ではロック無し（ベストエフォート）
    fcntl = None

VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# 分類の既定（charter の repo エントリ `- docs:` / `- tests:` / `- code:` で上書き可能）
# ---------------------------------------------------------------------------

DOC_EXTS = {".md", ".rst", ".adoc", ".txt"}
DEFAULT_DOCS_GLOBS = ["**/*.md", "**/*.rst", "**/*.adoc", "docs/**"]
DEFAULT_TESTS_GLOBS = [
    "tests/**", "test/**", "spec/**",
    "**/test_*.py", "**/*_test.py", "**/*_test.go",
    "**/*.test.*", "**/*.spec.*",
]
CODE_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rb", ".rs",
    ".java", ".kt", ".kts", ".scala", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs",
    ".swift", ".php", ".pl", ".pm", ".sh", ".bash", ".zsh", ".ps1", ".psm1",
    ".sql", ".proto", ".tf", ".vue", ".svelte",
}
# 参照トークンとして認める拡張子（ドキュメント中の `x.y` を「パスの主張」とみなす範囲）
REF_EXTS = CODE_EXTS | DOC_EXTS | {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".html", ".css", ".example"}

_ANNOT_RE = re.compile(r"coherence:\s*(doc|code|test)\s*=\s*([^\s`\"'<>]+)")
_INLINE_CODE_RE = re.compile(r"`([^`\n]{2,200})`")
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)#?\s]+)")
_IMPORT_RE = re.compile(r"^\s*(?:import\s+([\w.]+)|from\s+([\w.]+)\s+import\b)", re.M)
_TEST_STEM_RE = re.compile(r"^(?:test_(?P<a>.+)|(?P<b>.+?)_test|(?P<c>.+?)\.(?:test|spec))$")


# ---------------------------------------------------------------------------
# 汎用ユーティリティ
# ---------------------------------------------------------------------------

def _die(msg: str, code: int = 2) -> "None":
    print(f"[codd-gate] エラー: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _git(repo_dir: Path, *args: str, timeout: float = 120) -> "tuple[int, str]":
    try:
        p = subprocess.run(["git", "-C", str(repo_dir), *args],
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except Exception:
        return 1, ""


def _split_globs(v: str) -> "list[str]":
    return [g for g in re.split(r"[,\s]+", v or "") if g]


def _glob_match(glob: str, path: str) -> bool:
    """`**`（/ を跨ぐ）・`**/`（0 階層可）・ディレクトリ接頭辞（`docs` = `docs/**`）を受ける寛容マッチ。"""
    g = glob.strip().strip("/")
    if not g:
        return False
    if path == g or path.startswith(g + "/"):
        return True
    if fnmatch.fnmatch(path, g):
        return True
    if g.startswith("**/") and fnmatch.fnmatch(path, g[3:]):
        return True
    return False


def _matches_any(globs: "list[str]", path: str) -> bool:
    return any(_glob_match(g, path) for g in globs)


# ---------------------------------------------------------------------------
# charter の ## repos 読み取り（kiro-autonomous と同じ規約の互換サブセット）
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^##\s+(?P<key>[A-Za-z_]+)\s*$")
_REPO_ATTR_ALIASES = {
    "base": ("base", "base_branch", "ベース", "ベースブランチ"),
    "target": ("target", "target_branch", "ターゲット", "ターゲットブランチ"),
    "path": ("path", "dir", "folder", "subdir", "subpath", "パス", "ディレクトリ", "フォルダ"),
    "owns": ("owns", "own", "owned", "owns_paths", "paths", "所有", "担当", "管轄", "担当パス"),
    "desc": ("desc", "description", "説明", "内容", "内容物", "役割", "role"),
    # ここから下は codd-gate 専用キー（kiro-autonomous は未知キーとして無視する）
    "docs": ("docs", "doc", "ドキュメント", "文書"),
    "tests": ("tests", "test", "テスト"),
    "code": ("code", "コード", "実装"),
}


def _bullet_body(line: str) -> "tuple[int, str]":
    """箇条書き行なら (インデント, 本文) を返す。それ以外は (-1, "")。"""
    s = line.strip()
    if not s or s.startswith("<!--") or s[:1] not in "-*+":
        return -1, ""
    body = s[2:].strip() if s[1:2].isspace() else s[1:].strip()
    body = body.strip("`")
    return len(line) - len(line.lstrip(" \t")), body


def _attr(attrs: dict, key: str) -> str:
    for alias in _REPO_ATTR_ALIASES.get(key, (key,)):
        v = attrs.get(alias)
        if v:
            return str(v).strip().strip("`")
    return ""


def parse_charter_repos(text: str) -> "list[dict]":
    """charter.md の `## repos` を [{name,url,base,target,path,owns,docs,tests,code}] にする。"""
    lines, cur = [], None
    for line in text.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            cur = m.group("key").lower()
            continue
        if cur in ("repos", "repositories"):
            lines.append(line)
    entries, head_indent = [], None
    for line in lines:
        indent, body = _bullet_body(line)
        if indent < 0 or not body:
            continue
        # 行内コメント（# …）を落とす（URL 内 # は無いものとして扱う）
        body = re.split(r"\s+#\s", body)[0].strip()
        if not body:
            continue
        if head_indent is None or indent <= head_indent:
            head_indent = indent
            name, url = (body.split("=", 1) + [""])[:2] if "=" in body else ("", body)
            name, url = name.strip(), url.strip()
            if not name:
                tail = url.rstrip("/").split("/")[-1]
                name = tail[:-4] if tail.endswith(".git") else tail
            entries.append({"head": name, "url": url, "attrs": {}})
        elif entries:
            for sep in (":", "："):
                if sep in body:
                    k, v = body.split(sep, 1)
                    entries[-1]["attrs"][k.strip().lower()] = v.strip()
                    break
    out = []
    for e in entries:
        a = e["attrs"]
        out.append({
            "name": e["head"], "url": e["url"],
            "base": _attr(a, "base"), "target": _attr(a, "target") or _attr(a, "base"),
            "path": _attr(a, "path").strip("/"),
            "owns": _split_globs(_attr(a, "owns")),
            "docs": _split_globs(_attr(a, "docs")),
            "tests": _split_globs(_attr(a, "tests")),
            "code": _split_globs(_attr(a, "code")),
        })
    return out


# ---------------------------------------------------------------------------
# リポジトリ解決（identity = (url, path, base)。ローカル checkout は --repo-dir で対応付け）
# ---------------------------------------------------------------------------

class Repo:
    def __init__(self, name: str, url: str = "", base: str = "", target: str = "",
                 path: str = "", owns=None, docs=None, tests=None, code=None,
                 dir: "Path | None" = None):
        self.name, self.url, self.base, self.target = name, url, base, target
        self.path = path                    # モノレポ内の担当フォルダ（スキャンのルート絞り込み）
        self.owns = owns or []
        self.docs = docs or list(DEFAULT_DOCS_GLOBS)
        self.tests = tests or list(DEFAULT_TESTS_GLOBS)
        self.code = code or []              # 空 = 拡張子 CODE_EXTS で判定
        self.dir = dir                      # ローカル checkout（None = 未解決＝スキャン対象外）

    def classify(self, relpath: str) -> str:
        """doc > test > code の順で分類（.md 等のドキュメント拡張子は置き場所に依らず doc）。"""
        if Path(relpath).suffix.lower() in DOC_EXTS or _matches_any(self.docs, relpath):
            return "doc"
        if _matches_any(self.tests, relpath):
            return "test"
        if self.code:
            return "code" if _matches_any(self.code, relpath) else "other"
        return "code" if Path(relpath).suffix.lower() in CODE_EXTS else "other"


def _globs_value(v) -> "list[str]":
    """設定値のグロブ（カンマ/空白区切り文字列 or リスト）を正規化する。"""
    if isinstance(v, str):
        return _split_globs(v)
    return [str(g) for g in (v or [])]


def load_repos(charter_path: "Path | None", conf: dict,
               repo_dirs: "dict[str, Path]") -> "list[Repo]":
    """リポジトリレジストリを解決する（上から優先）:
    1. --charter … kiro-autonomous **連携アダプタ**（charter.md の ## repos を読む。単体では不要）
    2. 設定ファイルの `repos:` … codd-gate ネイティブのレジストリ（charter 書式に依存しない）
    3. --repo-dir の名前をそのまま repo にする（グロブは既定）
    4. どれも無ければカレントディレクトリを単一 repo `default` として扱う
    ローカル checkout は常に CLI の --repo-dir が設定より勝つ。"""
    if charter_path:
        if not charter_path.is_file():
            _die(f"charter が見つかりません: {charter_path}")
        specs = parse_charter_repos(charter_path.read_text(encoding="utf-8"))
        if not specs:
            _die(f"charter に ## repos がありません: {charter_path}")
        repos = []
        for s in specs:
            repos.append(Repo(name=s["name"], url=s["url"], base=s["base"], target=s["target"],
                              path=s["path"], owns=s["owns"], docs=s["docs"] or None,
                              tests=s["tests"] or None, code=s["code"] or None,
                              dir=repo_dirs.get(s["name"])))
        return repos
    conf_repos = conf.get("repos") or {}
    if conf_repos:                              # ネイティブレジストリ（identity は (url, path, base)）
        repos = []
        for name in sorted(conf_repos):
            s = conf_repos[name] or {}
            if not isinstance(s, dict):
                _die(f"設定 repos.{name} はマッピングで書いてください（dir/base/docs/tests/code…）")
            d = repo_dirs.get(str(name)) or (
                Path(str(s["dir"])).expanduser() if s.get("dir") else None)
            repos.append(Repo(
                name=str(name), url=str(s.get("url", "") or ""),
                base=str(s.get("base", "") or ""),
                target=str(s.get("target", "") or s.get("base", "") or ""),
                path=str(s.get("path", "") or "").strip("/"),
                docs=_globs_value(s.get("docs")) or None,
                tests=_globs_value(s.get("tests")) or None,
                code=_globs_value(s.get("code")) or None, dir=d))
        return repos
    if repo_dirs:                               # レジストリ無し = --repo-dir の名前をそのまま repo にする
        return [Repo(name=n, dir=d) for n, d in sorted(repo_dirs.items())]
    return [Repo(name="default", dir=Path.cwd())]


# ---------------------------------------------------------------------------
# --sync: 共有 git キャッシュ + worktree で url-only repo を最新の base で実体化する
#   （docs/designs/git-worktree-cache-pattern.md 準拠。フル clone はしない——ミラーは初回のみ
#     --mirror --filter=blob:none、以後は増分 fetch、worktree 生成はネットワークゼロ。
#     ミラー root は kiro-flow / kiro-autonomous と共有: KIRO_GIT_CACHE_DIR / $TMPDIR/kiro-git-cache）
#   INV-1 鮮度: 毎回 fetch → fetch 後に解決した SHA で worktree を作る（古い cache を黙って使わない）
#   INV-2 保全: URL ロックで直列化・gc.auto=0・破損検知で nuke & re-mirror
#   INV-3 下限: 全滅時は従来の浅 clone（--depth 1）へフォールバック。それも失敗なら未解決のまま
#     （成果の無い場所で偽判定しない——チェック対象に選べば exit 2）
#   dir が解決済みの repo には触れない（作業ツリーそのものが判定対象。fetch も clone もしない）。
# ---------------------------------------------------------------------------
_CACHE_CORRUPT = ("not a git repository", "bad object", "corrupt", "broken link",
                  "unable to read", "object directory", "fatal: bad")


def _cache_root() -> str:
    return os.environ.get("KIRO_GIT_CACHE_DIR") or os.path.join(
        tempfile.gettempdir(), "kiro-git-cache")


def _cache_path(url: str) -> str:
    return os.path.join(_cache_root(), hashlib.sha1(url.strip().encode()).hexdigest() + ".git")


@contextlib.contextmanager
def _cache_lock(url: str):
    root = _cache_root()
    os.makedirs(root, exist_ok=True)
    lock = os.path.join(root, hashlib.sha1(url.strip().encode()).hexdigest() + ".lock")
    if fcntl is None:
        yield
        return
    f = open(lock, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()


def _mirror_clone(url: str, cache: str) -> bool:
    """bare ミラーを作る（partial 非対応サーバには filter 無しで再試行）。フル clone はここでも不要。"""
    shutil.rmtree(cache, ignore_errors=True)
    os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
    for cmd in (["git", "clone", "--mirror", "--filter=blob:none", url, cache],
                ["git", "clone", "--mirror", url, cache]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is not None and r.returncode == 0:
            _git(Path(cache), "config", "gc.auto", "0")
            _git(Path(cache), "config", "remote.origin.mirror", "false")
            return True
        shutil.rmtree(cache, ignore_errors=True)
    return False


def _ensure_mirror(url: str) -> "str | None":
    cache = _cache_path(url)
    rc, _ = _git(Path(cache), "rev-parse", "--git-dir", timeout=30)
    if os.path.isdir(cache) and rc == 0:
        return cache
    return cache if _mirror_clone(url, cache) else None


def _mirror_fetch(cache: str) -> bool:
    """INV-1: 増分 fetch。破損系エラーは False（呼び出し側が nuke & re-mirror）。"""
    for i in range(3):
        rc, _ = _git(Path(cache), "fetch", "--prune", "--no-tags", "origin",
                     "+refs/heads/*:refs/heads/*", timeout=600)
        if rc == 0:
            return True
        if i < 2:
            time.sleep(1 + i)
    return False


def provision_repo(url: str, branch: str, dest: str) -> "str | None":
    """url の branch（空なら既定 HEAD）の最新を dest に detached worktree として実体化する。
    失敗時は浅 clone フォールバック、それも失敗なら None（未解決のまま＝偽判定しない）。"""
    try:
        with _cache_lock(url):
            cache = _ensure_mirror(url)
            if cache and not _mirror_fetch(cache):
                shutil.rmtree(cache, ignore_errors=True)      # 自己修復: nuke & re-mirror
                cache = _ensure_mirror(url)
                cache = cache if (cache and _mirror_fetch(cache)) else None
            if cache:
                ref = f"refs/heads/{branch}" if branch else "HEAD"
                rc, sha = _git(Path(cache), "rev-parse", "--verify", "--quiet",
                               f"{ref}^{{commit}}", timeout=30)
                if rc == 0 and sha.strip():
                    dest = os.path.abspath(dest)
                    for _ in range(2):
                        rc, _out = _git(Path(cache), "worktree", "add", "--detach",
                                        "--force", dest, sha.strip(), timeout=300)
                        if rc == 0:
                            return dest
                        _git(Path(cache), "worktree", "prune", timeout=60)
                        shutil.rmtree(dest, ignore_errors=True)
    except Exception:  # noqa: BLE001 — cache 系の想定外失敗は浅 clone へ
        pass
    cmd = ["git", "clone", "--depth", "1"] + (["--branch", branch] if branch else []) + [url, dest]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return dest if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def sync_repos(repos: "list[Repo]") -> "list[tuple[str, str]]":
    """dir 未解決かつ url を持つ repo を、最新の base で worktree として実体化する（--sync）。
    dir 解決済みの repo には触れない。返り値は後始末用の (url, dest) リスト。"""
    synced: "list[tuple[str, str]]" = []
    for r in repos:
        if r.dir is not None or not r.url:
            continue
        safe = re.sub(r"[^\w.-]", "_", r.name)
        dest = tempfile.mkdtemp(prefix=f"codd-gate-sync-{safe}-")
        got = provision_repo(r.url, r.base, dest)
        if got:
            r.dir = Path(got)
            synced.append((r.url, got))
        else:
            shutil.rmtree(dest, ignore_errors=True)
            print(f"[codd-gate] 警告: repo '{r.name}' を実体化できません（{r.url} {r.base or 'HEAD'}）",
                  file=sys.stderr)
    return synced


def cleanup_synced(synced: "list[tuple[str, str]]") -> None:
    """--sync が作った一時 worktree を回収する（共有ミラー本体は残す＝次回は増分 fetch だけ）。"""
    for url, dest in synced:
        shutil.rmtree(dest, ignore_errors=True)
        try:
            with _cache_lock(url):
                cache = _cache_path(url)
                if os.path.isdir(cache):
                    _git(Path(cache), "worktree", "prune", timeout=60)
        except Exception:  # noqa: BLE001
            pass


def repo_files(repo: Repo) -> "list[str]":
    """スキャン対象ファイル（repo.path 配下・repo.path 相対）。git 管理下が正、無ければ walk。"""
    assert repo.dir is not None
    rc, out = _git(repo.dir, "ls-files", "-z", "--", repo.path or ".")
    if rc == 0:
        files = [f for f in out.split("\0") if f]
        rc2, out2 = _git(repo.dir, "ls-files", "-z", "--others", "--exclude-standard",
                         "--", repo.path or ".")
        if rc2 == 0:
            files += [f for f in out2.split("\0") if f]
    else:
        root = repo.dir / repo.path if repo.path else repo.dir
        files = [str(p.relative_to(repo.dir)) for p in root.rglob("*")
                 if p.is_file() and ".git" not in p.parts]
    if repo.path:
        pre = repo.path + "/"
        files = [f[len(pre):] for f in files if f.startswith(pre)]
    # index に残っていても作業ツリーから消えたファイルは「実在しない」（削除の追随漏れを参照切れとして検出する）
    return sorted({f for f in files if _abs(repo, f).is_file()})


def _abs(repo: Repo, relpath: str) -> Path:
    return (repo.dir / repo.path / relpath) if repo.path else (repo.dir / relpath)


# ---------------------------------------------------------------------------
# 参照抽出（決定的。注釈 > 自動推定）
# ---------------------------------------------------------------------------

def _pathlike(tok: str) -> "str | None":
    """トークンが「パスの主張」に見えるなら正規化して返す（曖昧・コマンド類は None）。"""
    tok = tok.strip().rstrip(".,;:)")
    if tok.startswith("./"):
        tok = tok[2:]
    if not tok or len(tok) > 200 or tok.startswith(("http://", "https://", "mailto:", "#", "/")):
        return None
    if any(c in tok for c in " \t|$&;<>\"'*?{}()="):
        return None
    if "/" in tok:
        return tok.rstrip("/")
    return tok if Path(tok).suffix.lower() in REF_EXTS else None


def extract_refs(kind: str, text: str, repo_names: "set[str]") -> "list[dict]":
    """ファイル本文から参照候補 [{token, line, via, rel}] を返す。
    via: annot（明示注釈・最優先）/ inline（バッククォート）/ link（md リンク）/ import（python）。
    rel: 注釈で宣言された相手の種別（doc/code/test。自動推定は None）。"""
    refs, fence = [], False
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        for m in _ANNOT_RE.finditer(line):
            for tok in m.group(2).split(","):
                t = tok.strip()
                if t:
                    refs.append({"token": t, "line": i, "via": "annot", "rel": m.group(1)})
        if fence:
            continue                          # コードフェンス内はサンプルの可能性が高い（注釈は上で拾済み）
        if kind == "doc":
            for m in _INLINE_CODE_RE.finditer(line):
                t = _pathlike(m.group(1))
                if t:
                    refs.append({"token": t, "line": i, "via": "inline", "rel": None})
            for m in _MD_LINK_RE.finditer(line):
                t = _pathlike(m.group(1))
                if t:
                    refs.append({"token": t, "line": i, "via": "link", "rel": None})
        elif kind == "test":
            for m in _INLINE_CODE_RE.finditer(line):
                t = _pathlike(m.group(1))
                if t and "/" in t:
                    refs.append({"token": t, "line": i, "via": "inline", "rel": None})
    if kind == "test":
        for m in _IMPORT_RE.finditer(text):
            mod = m.group(1) or m.group(2)
            line = text[:m.start()].count("\n") + 1
            refs.append({"token": mod, "line": line, "via": "import", "rel": None})
        for m in re.finditer(r"[\"']([\w./-]+/[\w./-]+)[\"']", text):
            t = _pathlike(m.group(1))
            if t:
                line = text[:m.start()].count("\n") + 1
                refs.append({"token": t, "line": line, "via": "inline", "rel": None})
    # repo プレフィックス（name:path）はそのまま通す（解決側で処理）
    return refs


def _import_candidates(mod: str) -> "list[str]":
    dotted = mod.split(".")
    out = ["/".join(dotted) + ".py", "/".join(dotted) + "/__init__.py"]
    if len(dotted) > 1:
        out += ["/".join(dotted[:-1]) + ".py"]
    return out


class Index:
    """スキャン済み repo の逆引き（正確なパス・ディレクトリ接頭辞・basename/stem）。"""

    def __init__(self):
        self.files: "dict[str, set[str]]" = {}          # repo -> relpaths
        self.by_base: "dict[str, dict[str, list[str]]]" = {}   # repo -> basename -> paths
        self.by_stem: "dict[str, dict[str, list[str]]]" = {}   # repo -> stem -> code paths

    def add(self, repo: str, files: "list[str]", kinds: "dict[str, str]"):
        self.files[repo] = set(files)
        bb, bs = {}, {}
        for f in files:
            bb.setdefault(Path(f).name, []).append(f)
            if kinds.get(f) == "code":
                bs.setdefault(Path(f).stem, []).append(f)
        self.by_base[repo], self.by_stem[repo] = bb, bs

    def resolve(self, token: str, repo: str) -> "tuple[str, str] | str | None":
        """(repo, path)＝ノード解決 / "dir"＝ディレクトリ参照 / "ext"＝未スキャン repo / None＝未解決。"""
        tok, target_repo = token, repo
        if ":" in token:
            pre, rest = token.split(":", 1)
            if pre in self.files:
                target_repo, tok = pre, rest.strip("/")
            elif pre and not pre.startswith("."):
                return "ext"                    # レジストリ外/未スキャン repo への明示参照は判定しない
        order = [target_repo] + sorted(r for r in self.files if r != target_repo)
        if token != tok or ":" in token:
            order = [target_repo]
        for r in order:
            if tok in self.files[r]:
                return (r, tok)
        for r in order:
            if any(f.startswith(tok + "/") for f in self.files[r]):
                return "dir"
        if "/" not in tok:
            hits = self.by_base.get(target_repo, {}).get(tok, [])
            if len(hits) == 1:
                return (target_repo, hits[0])
            return "dir" if hits else None      # 同名複数は曖昧＝判定しない（None と区別して broken にしない）
        return None


def _node(repo: str, path: str) -> str:
    return f"{repo}:{path}"


def build_map(repos: "list[Repo]") -> dict:
    """接続マップを毎回フレッシュに構築する（キャッシュを done 判定に使わない＝偽グリーン対策）。"""
    scanned = [r for r in repos if r.dir is not None and r.dir.is_dir()]
    idx, kinds_by_repo, files_by_repo = Index(), {}, {}
    for r in scanned:
        files = repo_files(r)
        kinds = {f: r.classify(f) for f in files}
        files_by_repo[r.name], kinds_by_repo[r.name] = files, kinds
        idx.add(r.name, files, kinds)
    nodes, edges, broken = {}, [], []
    for r in scanned:
        for f in files_by_repo[r.name]:
            k = kinds_by_repo[r.name][f]
            if k != "other":
                nodes[_node(r.name, f)] = {"kind": k}
    seen_edges = set()

    def add_edge(src: str, dst: str, kind: str, evidence: str):
        key = (src, dst, kind)
        if src != dst and key not in seen_edges:
            seen_edges.add(key)
            edges.append({"src": src, "dst": dst, "kind": kind, "evidence": evidence})

    for r in scanned:
        for f in files_by_repo[r.name]:
            kind = kinds_by_repo[r.name][f]
            if kind == "other":
                continue
            try:
                text = _abs(r, f).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            me = _node(r.name, f)
            for ref in extract_refs(kind, text, set(idx.files)):
                tok, via = ref["token"], ref["via"]
                if via == "import":
                    hit = None
                    for cand in _import_candidates(tok):
                        matches = [p for p in files_by_repo[r.name]
                                   if p == cand or p.endswith("/" + cand)]
                        if len(matches) == 1:
                            hit = (r.name, matches[0])
                            break
                    if hit:
                        add_edge(me, _node(*hit), "tests", f"{f}:{ref['line']} import {tok}")
                    continue
                got = idx.resolve(tok, r.name)
                if got in ("dir", "ext"):
                    continue
                if got is None:
                    if "/" in tok or via == "annot":
                        broken.append({"node": me, "token": tok, "line": ref["line"], "via": via})
                    continue
                trepo, tpath = got
                tkind = kinds_by_repo[trepo].get(tpath, "other")
                ev = f"{f}:{ref['line']} ({via})"
                if via == "annot":
                    rel = ref["rel"]
                    if rel == "doc":            # code/test 側から doc を宣言 → doc→me の documents
                        add_edge(_node(trepo, tpath), me, "documents", ev)
                    elif rel == "test":
                        add_edge(_node(trepo, tpath), me, "tests", ev)
                    else:                       # doc/test 側から code を宣言
                        add_edge(me, _node(trepo, tpath), "tests" if kind == "test" else "documents", ev)
                elif kind == "doc":
                    add_edge(me, _node(trepo, tpath), "documents", ev)
                elif kind == "test" and tkind == "code":
                    add_edge(me, _node(trepo, tpath), "tests", ev)
            if kind == "test":                  # 命名規約 test_x ↔ x（同一 repo・一意のときだけ）
                m = _TEST_STEM_RE.match(Path(f).stem)
                if m:
                    stem = m.group("a") or m.group("b") or m.group("c")
                    hits = [p for p in idx.by_stem[r.name].get(stem, []) if p != f]
                    if len(hits) == 1:
                        add_edge(me, _node(r.name, hits[0]), "tests", f"{f} 命名規約 ({stem})")
    documented = {e["dst"] for e in edges if e["kind"] == "documents"}
    tested = {e["dst"] for e in edges if e["kind"] == "tests"}
    orphans = {
        "undocumented": sorted(n for n, v in nodes.items() if v["kind"] == "code" and n not in documented),
        "untested": sorted(n for n, v in nodes.items() if v["kind"] == "code" and n not in tested),
    }
    meta = {}
    for r in scanned:
        _, head = _git(r.dir, "rev-parse", "HEAD")
        _, branch = _git(r.dir, "rev-parse", "--abbrev-ref", "HEAD")
        meta[r.name] = {"url": r.url, "base": r.base, "path": r.path,
                        "dir": str(r.dir), "head": head.strip(), "branch": branch.strip()}
    unscanned = sorted(r.name for r in repos if r not in scanned)
    return {"version": 1, "repos": meta, "unscanned": unscanned, "nodes": nodes,
            "edges": sorted(edges, key=lambda e: (e["src"], e["dst"], e["kind"])),
            "broken_refs": sorted(broken, key=lambda b: (b["node"], b["line"], b["token"])),
            "orphans": orphans}


# ---------------------------------------------------------------------------
# 差分と影響分類（Green / Amber / Gray / Followup）
# ---------------------------------------------------------------------------

def changed_files(repo: Repo, base: str) -> "dict[str, str]":
    """base..作業ツリー（staged/unstaged 込み）＋未追跡を {relpath: status} で返す。"""
    assert repo.dir is not None
    rc, out = _git(repo.dir, "diff", "--name-status", "-z", base, "--", repo.path or ".")
    if rc != 0:
        _die(f"repo '{repo.name}' で base '{base}' の差分を取得できません（rev を確認）")
    changed: "dict[str, str]" = {}
    parts = [p for p in out.split("\0") if p]
    i = 0
    while i < len(parts):
        st = parts[i][:1]
        if st in ("R", "C") and i + 2 < len(parts):
            changed[parts[i + 1]] = "D"
            changed[parts[i + 2]] = "A"
            i += 3
        elif i + 1 < len(parts):
            changed[parts[i + 1]] = st
            i += 2
        else:
            break
    rc, out = _git(repo.dir, "ls-files", "-z", "--others", "--exclude-standard", "--", repo.path or ".")
    if rc == 0:
        for f in out.split("\0"):
            if f:
                changed.setdefault(f, "A")
    if repo.path:
        pre = repo.path + "/"
        changed = {f[len(pre):]: s for f, s in changed.items() if f.startswith(pre)}
    return changed


def _select_target(repos: "list[Repo]", name: "str | None") -> Repo:
    scanned = [r for r in repos if r.dir is not None and r.dir.is_dir()]
    if name:
        for r in scanned:
            if r.name == name:
                return r
        _die(f"repo '{name}' のディレクトリが未解決です（--repo-dir {name}=<dir> を指定）")
    cwd = Path.cwd().resolve()
    hits = [r for r in scanned if r.dir.resolve() == cwd]
    if len(hits) == 1:
        return hits[0]
    if len(scanned) == 1:
        return scanned[0]
    _die("差分を判定する repo が曖昧です（--repo <name> で指定）")


def classify_impact(mapdata: dict, repos: "list[Repo]", target: Repo, base: str) -> dict:
    """差分スコープの一貫性を分類する。
    green    … 変更されたノードの同一 repo 側カウンタパートも同じ差分で更新済み（整合した変更）
    amber    … ドリフト（NG）: linked doc 未更新 / 変更ファイルの壊れた参照 / 削除で参照が浮いた
    gray     … 情報: 変更されたがマップに接続の無いコード（未文書化の新規/既存）
    followup … 別 repo 側のカウンタパート（この差分では検証不能→タスク化して返す）"""
    changed = changed_files(target, base)
    changed_nodes = {_node(target.name, f) for f in changed}
    nodes, edges = mapdata["nodes"], mapdata["edges"]
    docs_of, tests_of = {}, {}
    for e in edges:
        (docs_of if e["kind"] == "documents" else tests_of).setdefault(e["dst"], []).append(e)
    broken_by_node: "dict[str, list]" = {}
    for b in mapdata["broken_refs"]:
        broken_by_node.setdefault(b["node"], []).append(b)
    green, amber, gray, followup = [], [], [], []
    for f in sorted(changed):
        st, me = changed[f], _node(target.name, f)
        kind = nodes.get(me, {}).get("kind")
        if st == "D":
            # 削除されたファイルを未更新の doc/test がまだ参照していないか（壊れた参照として現れる）
            for b in mapdata["broken_refs"]:
                tok = b["token"].split(":", 1)[-1]
                if (f == tok or f.endswith("/" + tok)) and b["node"] not in changed_nodes:
                    amber.append({"type": "dangling-ref", "node": b["node"], "counterpart": me,
                                  "detail": f"{b['node']} が削除された {f} を参照したまま（{b['token']} 行{b['line']}）"})
            continue
        if kind is None:
            continue                            # 分類対象外（other）
        mine_broken = broken_by_node.get(me, [])
        if mine_broken:
            for b in mine_broken:
                amber.append({"type": "broken-ref", "node": me, "counterpart": b["token"],
                              "detail": f"{f} 行{b['line']} の参照 {b['token']} が解決できない"})
            continue
        if kind == "code":
            linked_docs = docs_of.get(me, [])
            stale, cross, ok = [], [], []
            for e in linked_docs:
                src_repo = e["src"].split(":", 1)[0]
                if e["src"] in changed_nodes:
                    ok.append(e["src"])
                elif src_repo == target.name:
                    stale.append(e)
                else:
                    cross.append(e)
            for e in stale:
                amber.append({"type": "doc-stale", "node": me, "counterpart": e["src"],
                              "detail": f"{f} が変更されたが {e['src']} が未更新（根拠: {e['evidence']}）"})
            for e in cross:
                followup.append({"type": "doc-stale-cross", "node": me, "counterpart": e["src"],
                                 "detail": f"{f} の変更に対し別 repo の {e['src']} の追随が必要"})
            if not linked_docs and not tests_of.get(me):
                gray.append({"type": "unmapped", "node": me,
                             "detail": f"{f} はドキュメント・テストのどちらにも接続が無い"
                                       + ("（新規）" if st == "A" else "")})
            elif not stale:
                green.append({"node": me, "detail": f"{f}（接続 {len(linked_docs) + len(tests_of.get(me, []))} 本・整合）"})
        else:                                   # doc / test の変更で参照が全て解決 → green
            green.append({"node": me, "detail": f"{f}（参照は全て解決）"})
    return {"base": base, "repo": target.name,
            "changed": {f: s for f, s in sorted(changed.items())},
            "green": green, "amber": amber, "gray": gray, "followup": followup}


# ---------------------------------------------------------------------------
# タスク生成（kiro-autonomous enqueue --json / inbox 形式）
# ---------------------------------------------------------------------------

def _self_cmd() -> str:
    return "codd-gate"


def _task_id(kind: str, *parts: str) -> str:
    """kiro-autonomous の id 規約（[A-Za-z0-9_-]・48 字）に収まる決定的 id。同じ発見は常に同じ id
    （＝intake_cmd の冪等キー）。末尾ハッシュで切り詰めによる別発見同士の衝突を防ぐ。"""
    raw = "-".join(p.split(":", 1)[-1] for p in parts)
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")[:28].strip("-")
    digest = hashlib.sha1("|".join((kind,) + parts).encode("utf-8")).hexdigest()[:6]
    return f"codd-{kind}-{slug}-{digest}"   # 5 + kind(≤6) + 1 + 28 + 1 + 6 ≤ 47 < 48


def tasks_from_impact(imp: dict, priority: int) -> "list[dict]":
    out = []
    for a in imp["amber"]:
        repo, path = a["node"].split(":", 1)
        if a["type"] == "doc-stale":
            _, doc = a["counterpart"].split(":", 1)
            out.append({
                "id": _task_id("doc", a["counterpart"], path),
                "title": f"{path} の変更をドキュメント {doc} へ反映する（repo {repo}）",
                "verify": f"{_self_cmd()} check --repo-dir {repo}=. --doc {doc} --code {path} --fresh",
                "paths": doc, "priority": priority, "expect": "changes",
                "note": a["detail"],
            })
        elif a["type"] in ("broken-ref", "dangling-ref"):
            nrepo, npath = a["node"].split(":", 1)
            out.append({
                "id": _task_id("ref", a["node"]),
                "title": f"{npath} の壊れた参照を修正する（repo {nrepo}）",
                "verify": f"{_self_cmd()} check --repo-dir {nrepo}=. --refs {npath}",
                "paths": npath, "priority": priority, "expect": "changes",
                "note": a["detail"],
            })
    for c in imp["followup"]:
        drepo, doc = c["counterpart"].split(":", 1)
        _, path = c["node"].split(":", 1)
        out.append({
            "id": _task_id("cross", c["counterpart"], path),
            "title": f"{c['node']} の変更を {drepo} 側のドキュメント {doc} へ反映する",
            "accept": f"{doc} が {path} の最新の変更内容と矛盾しない記述になっている",
            "workspace": drepo, "paths": doc, "priority": priority,
            "note": c["detail"],
        })
    for g in imp["gray"]:
        repo, path = g["node"].split(":", 1)
        out.append({
            "id": _task_id("map", g["node"]),
            "title": f"{path} をドキュメント/テストに接続する（repo {repo}）",
            "verify": f"{_self_cmd()} check --repo-dir {repo}=. --covered {path} --need doc",
            "paths": path, "priority": max(priority - 1, 0),
            "note": g["detail"] + " — 文書化するか、注釈 `coherence: doc=<path>` で接続を宣言する",
        })
    return out


def tasks_from_debt(mapdata: dict, priority: int, limit: int, cohort: bool = False) -> "list[dict]":
    """負債をタスク化する。壊れた参照は案件毎（各々ユニークな修正）。未文書化・未テストは同種作業の
    繰り返しなので、--cohort なら repo 単位の cohort（kiro-autonomous の pilot-then-batch: 1 件を
    人の検収で固めてから残りへ展開）にまとめ、後段のタスク分解を kiro-autonomous に委ねる。"""
    out = []
    for b in mapdata["broken_refs"][:limit]:
        repo, path = b["node"].split(":", 1)
        out.append({
            "id": _task_id("ref", b["node"], b["token"]),
            "title": f"{path} の壊れた参照 {b['token']} を修正する（repo {repo}）",
            "verify": f"{_self_cmd()} check --repo-dir {repo}=. --refs {path}",
            "paths": path, "priority": priority, "expect": "changes",
            "note": f"{path} 行{b['line']} の {b['token']} が実在しない（{b['via']}）",
        })
    for kind, need, label in (("doc", "doc", "を文書化する"), ("test", "test", "のテストを追加する")):
        nodes = mapdata["orphans"]["undocumented" if kind == "doc" else "untested"]
        by_repo: "dict[str, list[str]]" = {}
        for n in nodes:
            repo, path = n.split(":", 1)
            by_repo.setdefault(repo, []).append(path)
        for repo in sorted(by_repo):
            items = by_repo[repo][:limit]
            if cohort and len(items) >= 2:
                out.append({
                    "id": _task_id("cohort", f"{repo}:{kind}", *items),
                    "title": f"{{item}} {label}（repo {repo}）",
                    "verify": f"{_self_cmd()} check --repo-dir {repo}=. --covered {{item}} --need {need}",
                    "cohort_items": items, "priority": max(priority - 1, 0),
                    "note": f"接続マップ上で {need} 接続の無い code（{len(items)} 件）。"
                            "pilot 1 件を検収して指示を固めてから残りへ展開される",
                })
                continue
            for path in items:
                out.append({
                    "id": _task_id(kind, f"{repo}:{path}"),
                    "title": f"{path} {label}（repo {repo}）",
                    "verify": f"{_self_cmd()} check --repo-dir {repo}=. --covered {path} --need {need}",
                    "paths": path, "priority": max(priority - 1, 0),
                    "note": "接続マップ上でどの" + ("ドキュメント" if kind == "doc" else "テスト")
                            + "からも参照されていない",
                })
    return out


# ---------------------------------------------------------------------------
# check（修復タスクの verify 用の状態アサーション。履歴でなく現在の状態を見る）
# ---------------------------------------------------------------------------

def _last_change_ts(repo: Repo, relpath: str) -> "int | None":
    """ファイルの実質最終変更時刻。未コミット変更があれば「今」、無ければ最終コミット時刻。"""
    ap = _abs(repo, relpath)
    if not ap.exists():
        return None
    full = f"{repo.path}/{relpath}" if repo.path else relpath
    rc, out = _git(repo.dir, "status", "--porcelain", "--", full)
    if rc == 0 and out.strip():
        return int(time.time())
    rc, out = _git(repo.dir, "log", "-1", "--format=%ct", "--", full)
    if rc == 0 and out.strip():
        return int(out.strip().splitlines()[0])
    return int(ap.stat().st_mtime)


def cmd_check(args, repos: "list[Repo]") -> int:
    mapdata = build_map(repos)

    def find_node(rel: str) -> "str | None":
        hits = [n for n in mapdata["nodes"] if n.split(":", 1)[1] == rel]
        return hits[0] if len(hits) == 1 else None

    if args.refs:
        node = find_node(args.refs)
        if node is None:
            print(f"NG: {args.refs} がスキャン対象に見つからない（または曖昧）")
            return 1
        bad = [b for b in mapdata["broken_refs"] if b["node"] == node]
        if bad:
            for b in bad:
                print(f"NG: {args.refs} 行{b['line']} の参照 {b['token']} が解決できない")
            return 1
        print(f"OK: {args.refs} の参照は全て解決")
        return 0
    if args.covered:
        node = find_node(args.covered)
        need = set(_split_globs(args.need or "doc"))
        if node is None:
            print(f"NG: {args.covered} が見つからない")
            return 1
        have = {("doc" if e["kind"] == "documents" else "test")
                for e in mapdata["edges"] if e["dst"] == node}
        missing = sorted(need - have)
        if missing:
            print(f"NG: {args.covered} に {'/'.join(missing)} の接続が無い")
            return 1
        print(f"OK: {args.covered} は {'/'.join(sorted(need))} に接続済み")
        return 0
    if args.doc and args.code:
        dnode, cnode = find_node(args.doc), find_node(args.code)
        if dnode is None or cnode is None:
            print(f"NG: {args.doc} / {args.code} が見つからない")
            return 1
        edge = [e for e in mapdata["edges"]
                if e["kind"] == "documents" and e["src"] == dnode and e["dst"] == cnode]
        if not edge:
            print(f"NG: {args.doc} → {args.code} の documents 接続が無い")
            return 1
        if [b for b in mapdata["broken_refs"] if b["node"] == dnode]:
            print(f"NG: {args.doc} に壊れた参照が残っている")
            return 1
        if args.fresh:
            by_name = {r.name: r for r in repos}
            drepo, dpath = dnode.split(":", 1)
            crepo, cpath = cnode.split(":", 1)
            dts = _last_change_ts(by_name[drepo], dpath)
            cts = _last_change_ts(by_name[crepo], cpath)
            if dts is None or cts is None or dts < cts:
                print(f"NG: {args.doc} が {args.code} より古い（doc={dts} code={cts}）")
                return 1
        print(f"OK: {args.doc} → {args.code} は接続済み" + ("・鮮度 OK" if args.fresh else ""))
        return 0
    _die("check には --refs / --covered / (--doc と --code) のいずれかが必要です")
    return 2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_config(path: "Path | None") -> dict:
    cands = [path] if path else [Path(".kiro") / f"codd-gate.{ext}" for ext in ("yaml", "yml", "json")] \
        + [Path.home() / ".kiro" / f"codd-gate.{ext}" for ext in ("yaml", "yml", "json")]
    for p in cands:
        if p and p.is_file():
            text = p.read_text(encoding="utf-8")
            if p.suffix in (".yaml", ".yml"):
                try:
                    import yaml                     # 任意依存（無ければ JSON へ）
                    return yaml.safe_load(text) or {}
                except ImportError:
                    pass
            try:
                return json.loads(text)
            except ValueError:
                _die(f"設定ファイルを解釈できません: {p}")
    return {}


def _parse_repo_dirs(items: "list[str]", conf: dict) -> "dict[str, Path]":
    out: "dict[str, Path]" = {}
    for name, d in (conf.get("repo_dirs") or {}).items():
        out[str(name)] = Path(str(d)).expanduser()
    for it in items or []:
        if "=" in it:
            name, d = it.split("=", 1)
        else:
            name, d = "default", it
        out[name.strip()] = Path(d.strip()).expanduser()
    return out


def _print_summary(mapdata: dict) -> None:
    n = mapdata["nodes"]
    kinds = {"doc": 0, "code": 0, "test": 0}
    for v in n.values():
        kinds[v["kind"]] += 1
    print(f"ノード: doc {kinds['doc']} / code {kinds['code']} / test {kinds['test']}"
          f" ／ エッジ: {len(mapdata['edges'])}")
    o = mapdata["orphans"]
    print(f"負債: 壊れた参照 {len(mapdata['broken_refs'])} / 未文書化 {len(o['undocumented'])}"
          f" / 未テスト {len(o['untested'])}")
    for b in mapdata["broken_refs"][:10]:
        print(f"  - {b['node']} 行{b['line']}: {b['token']} が解決できない")
    if mapdata["unscanned"]:
        print(f"未スキャン repo（--repo-dir 未解決）: {', '.join(mapdata['unscanned'])}")


def main(argv: "list[str] | None" = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--charter", default=None,
                        help="kiro-autonomous 連携アダプタ: charter.md の ## repos をレジストリとして"
                             "読む（単体利用では設定ファイルの repos: を使う）")
    common.add_argument("--config", default=None,
                        help="設定ファイル（.kiro/codd-gate.{yaml,json}。repos:/repo_dirs:/map: を持てる）")
    common.add_argument("--repo-dir", action="append", default=[], metavar="NAME=DIR",
                        help="repo 名 → ローカル checkout の対応（複数可。NAME 省略は default）")
    common.add_argument("--sync", action="store_true",
                        help="dir 未解決で url を持つ repo を、共有ミラー＋worktree で最新の base に"
                             "実体化する（設定 sync: true でも可。フル clone なし＝ミラー初回のみ・"
                             "以後増分 fetch。ミラーは kiro ツール群と共有）")
    common.add_argument("--map", dest="map_path", default=None, help="マップの書き出し先（scan）")
    common.add_argument("--json", action="store_true", help="JSON で出力")

    ap = argparse.ArgumentParser(prog="codd-gate",
                                 description="doc/code/test の一貫性ゲート（CoDD 流用・kiro-autonomous プラグイン）")
    ap.add_argument("--version", action="version", version=f"codd-gate {VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scan", parents=[common], help="接続マップと負債の棚卸し")
    sp = sub.add_parser("impact", parents=[common], help="差分の Green/Amber/Gray/Followup 分類")
    sp.add_argument("--base", default=None, help="差分の基準 rev（既定 $KIRO_BASE_REV）")
    sp.add_argument("--repo", default=None, help="差分を取る repo 名（曖昧なとき必須）")
    sp = sub.add_parser("verify", parents=[common], help="一貫性ゲート（exit 0/1）")
    sp.add_argument("--base", default=None, help="差分の基準 rev（既定 $KIRO_BASE_REV。--debt 時は不要）")
    sp.add_argument("--repo", default=None)
    sp.add_argument("--strict", action="store_true", help="gray（未接続の変更）も NG にする")
    sp.add_argument("--strict-cross", action="store_true", help="followup（別 repo の追随）も NG にする")
    sp.add_argument("--debt", action="store_true", help="差分でなく全体負債をしきい値と突合する")
    sp.add_argument("--max-broken", type=int, default=None, help="--debt: 壊れた参照の許容数")
    sp.add_argument("--max-undocumented", type=int, default=None, help="--debt: 未文書化 code の許容数")
    sp.add_argument("--max-untested", type=int, default=None, help="--debt: 未テスト code の許容数")
    sp = sub.add_parser("tasks", parents=[common], help="修復タスクを enqueue --json 形式で出力")
    sp.add_argument("--base", default=None)
    sp.add_argument("--repo", default=None)
    sp.add_argument("--debt", action="store_true", help="全体負債からタスク化（既定は差分から）")
    sp.add_argument("--priority", type=int, default=1)
    sp.add_argument("--max", type=int, default=20, help="--debt: 種別ごとの上限件数")
    sp.add_argument("--cohort", action="store_true",
                    help="--debt: 未文書化/未テストを repo 単位の cohort（kiro-autonomous の "
                         "pilot-then-batch）にまとめる（後段のタスク分解を委ねる）")
    sp.add_argument("--inbox", default=None, help="標準出力でなく inbox ディレクトリへ 1 タスク 1 JSON で書く")
    sp = sub.add_parser("check", parents=[common], help="状態アサーション（修復タスクの verify 用）")
    sp.add_argument("--doc", default=None, help="ドキュメントの相対パス")
    sp.add_argument("--code", default=None, help="コードの相対パス")
    sp.add_argument("--fresh", action="store_true", help="doc が code より新しいことも要求")
    sp.add_argument("--refs", default=None, help="このファイルの参照が全て解決することを要求")
    sp.add_argument("--covered", default=None, help="このコードに接続があることを要求")
    sp.add_argument("--need", default="doc", help="--covered で要求する接続（doc,test）")

    args = ap.parse_args(argv)
    conf = _load_config(Path(args.config).expanduser() if args.config else None)
    charter = args.charter or conf.get("charter")
    repo_dirs = _parse_repo_dirs(args.repo_dir, conf)
    repos = load_repos(Path(charter).expanduser() if charter else None, conf, repo_dirs)
    synced = sync_repos(repos) if (args.sync or conf.get("sync")) else []
    try:
        return _run(args, conf, repos)
    finally:
        cleanup_synced(synced)                  # 一時 worktree を回収（共有ミラーは残す）


def _run(args, conf: dict, repos: "list[Repo]") -> int:
    scanned = [r for r in repos if r.dir is not None and r.dir.is_dir()]
    if not scanned:
        _die("スキャン可能な repo がありません（--repo-dir <name>=<dir> か --sync を指定）")

    if args.cmd == "check":
        return cmd_check(args, repos)

    mapdata = build_map(repos)
    map_path = args.map_path or conf.get("map")
    if args.cmd == "scan":
        out = Path(map_path) if map_path else Path(".codd-gate") / "map.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(mapdata, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                       encoding="utf-8")
        if args.json:
            print(json.dumps(mapdata, ensure_ascii=False, sort_keys=True))
        else:
            _print_summary(mapdata)
            print(f"マップ: {out}")
        return 0

    if args.cmd == "verify" and args.debt:
        o, findings = mapdata["orphans"], []
        for label, count, limit in (
                ("壊れた参照", len(mapdata["broken_refs"]), args.max_broken),
                ("未文書化", len(o["undocumented"]), args.max_undocumented),
                ("未テスト", len(o["untested"]), args.max_untested)):
            if limit is not None and count > limit:
                findings.append(f"{label} {count} 件 > 許容 {limit}")
        if args.json:
            print(json.dumps({"debt": {"broken": len(mapdata["broken_refs"]),
                                       "undocumented": len(o["undocumented"]),
                                       "untested": len(o["untested"])},
                              "findings": findings}, ensure_ascii=False))
        else:
            _print_summary(mapdata)
            for f in findings:
                print(f"NG: {f}")
        return 1 if findings else 0

    if args.cmd == "tasks" and args.debt:
        specs = tasks_from_debt(mapdata, args.priority, args.max, cohort=args.cohort)
        return _emit_tasks(specs, args)

    # ここから差分モード（impact / verify / tasks）
    base = getattr(args, "base", None) or os.environ.get("KIRO_BASE_REV", "")
    if not base:
        _die("差分の基準 rev がありません（--base か $KIRO_BASE_REV。全体負債は --debt）")
    target = _select_target(repos, getattr(args, "repo", None))
    imp = classify_impact(mapdata, repos, target, base)

    if args.cmd == "tasks":
        return _emit_tasks(tasks_from_impact(imp, args.priority), args)

    if args.json:
        print(json.dumps(imp, ensure_ascii=False, sort_keys=True))
    else:
        print(f"差分: {target.name} {base}..作業ツリー（{len(imp['changed'])} ファイル）")
        for label, items in (("GREEN", imp["green"]), ("AMBER", imp["amber"]),
                             ("GRAY", imp["gray"]), ("FOLLOWUP", imp["followup"])):
            for it in items:
                print(f"  [{label}] {it.get('detail', it['node'])}")
    if args.cmd == "impact":
        return 0
    ng = bool(imp["amber"]) or (args.strict and bool(imp["gray"])) \
        or (args.strict_cross and bool(imp["followup"]))
    if not args.json:
        print("NG: ドリフトあり — `codd-gate tasks` で修復タスクを生成できる" if ng else "OK: 一貫性ゲート通過")
    return 1 if ng else 0


def _emit_tasks(specs: "list[dict]", args) -> int:
    if args.inbox:
        d = Path(args.inbox)
        d.mkdir(parents=True, exist_ok=True)
        for s in specs:
            (d / f"{s['id']}.json").write_text(
                json.dumps(s, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"{len(specs)} タスクを {d} へ書き出しました")
    else:
        print(json.dumps(specs, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
