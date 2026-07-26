"""agentcore.repolocal — git URL の正規化一致と「このノードのローカルクローン」解決の単一定義。

## なぜここに 1 実装を置くか

同じ「2 つの git URL は同じリポジトリか」を、3 者が別々に実装していた:

- `agent_project/state.py:_same_git_remote` … 末尾 .git / スラッシュ / **ローカルパス絶対化**
- `agent_flow/gitcache.py:_same_repo`       … 末尾 .git / スラッシュ / **小文字化**
- `agent_flow/board.py:_norm_repo_url`      … 同上（入札選別用）

吸収規則が食い違うので、**同じ 2 つの URL が経路によって一致したりしなかったり**する。
`agentcore.nodeid` が解決したのと同型の問題（各エンジンの独自正規化で板に同じ PC が 2 ノードと
して現れた）なので、同じ流儀で 1 箇所に集約する。

## ノード固有のローカルクローン宣言（S3）

`local` は「手元にある同じリポジトリのクローンの絶対パス」で、あれば worker はネットワーク越しに
ミラーを取り直さず、そこから detached worktree を切れる（速い・オフラインでも動く）。

**宣言の置き場は各 PC の `~/.agents/agent-project.host.yaml` の `repos[]` だけ**。共有 repos.json に
書くと、その PC にしか存在しない絶対パスが状態リポジトリ経由で全 PC へ配られる（C3/C4 の元凶）。

    repos:
      - url: https://git.example.com/app.git
        local: /home/me/mirrors/app

読み手が agent-project / agent-flow / dashboard に跨るため、host.yaml の読み取りもここに置く
（agent-flow は agent-project の設定モジュールを import しない — R1）。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# host.yaml の探索順（`agent_project.resident_cli._find_host_config` と同じ規約）。
# 環境変数はテストが実ホームを汚さないための同じ seam。
_HOST_CONFIG_NAMES = ("agent-project.host.yaml", "agent-project.host.yml",
                      "agent-project.host.json")
_AGENTS_HOME_ENV = "AGENT_PROJECT_AGENTS_HOME"
_AGENTS_HOME_DIR = ".agents"


def normalize_repo_url(url: str) -> str:
    """git URL/パスの正規形。同じリポジトリを指す表記の揺れを吸収する。

    吸収するもの:
      - 末尾の `.git` とスラッシュ（`…/app.git` と `…/app/` は同じ）
      - 大小文字（ホスト名は DNS 上そもそも区別せず、パス側も実運用で揺れる）
      - ローカルパス表記の相対/`~`/シンボリックリンク（絶対化して比較する）

    ローカルパスだけ絶対化するのは、`/srv/repos/app` と `../repos/app` が同じ実体を指しうる
    ため。URL（`://` か `user@host:` を含む）は文字列として比較する——ネットワーク越しの
    同一性はこちらでは判定できないし、パスとして絶対化すると壊れる。
    """
    s = str(url or "").strip().rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    if not s:
        return ""
    if "://" not in s and not re.match(r"^[^/\\]+@[^/\\]+:", s):
        try:
            return str(Path(s).expanduser().resolve()).lower()
        except (OSError, RuntimeError, ValueError):
            return s.lower()
    return s.lower()


def same_repo(a: str, b: str) -> bool:
    """2 つの git URL/パスが同じリポジトリを指すか（どちらかが空なら False）。"""
    na, nb = normalize_repo_url(a), normalize_repo_url(b)
    return bool(na) and na == nb


def _agents_home() -> Path:
    override = os.environ.get(_AGENTS_HOME_ENV)
    return Path(override) if override else (Path.home() / _AGENTS_HOME_DIR)


def _find_host_config(explicit: "str | None" = None) -> "str | None":
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    for base in (Path.cwd(), _agents_home()):
        for name in _HOST_CONFIG_NAMES:
            p = base / name
            if p.is_file():
                return str(p)
    return None


def _read_config(path: str) -> dict:
    """host.yaml/host.json を読む（PyYAML が無ければ JSON のみ）。壊れていても例外にしない。

    ここは「ローカル最適化のヒントを読む」だけなので、設定ミスや PyYAML 不在で
    **呼び出し側を落とさない**（解決できなければミラー取得へ落ちるだけで動作は正しい）。
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    if path.lower().endswith((".yaml", ".yml")):
        try:
            import yaml  # type: ignore
        except ImportError:
            return {}
        try:
            return yaml.safe_load(text) or {}
        except Exception:       # noqa: BLE001 — YAML の構文エラー種別は実装依存
            return {}
    try:
        return json.loads(text) or {}
    except ValueError:
        return {}


def normalize_repos(raw) -> "list[dict]":
    """host.yaml の `repos:` を `[{url, local}, …]` へ正規化する。

    人が手で書くので型は保証されない。list（正）のほか、旧 mapping 形式（url -> local）と
    素の文字列列も受ける。壊れていても例外にせず拾える分だけ返す——設定ミスでノードが
    起動しない方が、ローカル最適化が効かないことより高くつく。
    """
    out: "list[dict]" = []
    if isinstance(raw, dict):
        for url, val in raw.items():
            if isinstance(val, dict):
                out.append({"url": str(url), **{k: str(v) for k, v in val.items() if v is not None}})
            elif val:
                out.append({"url": str(url), "local": str(val)})
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("url"):
                out.append({str(k): (str(v) if v is not None else "")
                            for k, v in item.items()})
            elif isinstance(item, str) and item.strip():
                out.append({"url": item.strip()})
    return out


def load_node_repos(path: "str | None" = None) -> "list[dict]":
    """このノードの `repos[]` 宣言（host.yaml）を読む。無ければ空リスト。"""
    found = _find_host_config(path)
    return normalize_repos(_read_config(found).get("repos")) if found else []


def resolve_local(url: str, repos=None, *, require_dir: bool = True) -> str:
    """URL に対応する **このノードのローカルクローン** のパス（無ければ ""）。

    `repos` を渡さなければ host.yaml から読む。`require_dir=False` は「宣言だけ確かめたい」
    用途（存在検査を呼び出し側で行う UI 等）向け。
    """
    if not str(url or "").strip():
        return ""
    for entry in (normalize_repos(repos) if repos is not None else load_node_repos()):
        local = str(entry.get("local") or "").strip()
        if not local or not same_repo(entry.get("url", ""), url):
            continue
        p = Path(local).expanduser()
        if not require_dir or p.is_dir():
            return str(p)
    return ""


def merge_local(spec: "dict | None", repos=None) -> "dict | None":
    """workspace spec に、このノードの `local` を埋めて返す（非破壊）。

    **既に `local` があれば尊重する**——依頼側が明示したパスや、上流で解決済みの値を
    後から握り潰さない。該当が無ければ spec をそのまま返す（`local` キーは足さない）。
    """
    if not isinstance(spec, dict) or str(spec.get("local") or "").strip():
        return spec
    local = resolve_local(str(spec.get("url") or ""), repos)
    return {**spec, "local": local} if local else spec
