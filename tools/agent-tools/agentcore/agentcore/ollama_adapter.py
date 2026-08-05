"""Ollama の非ストリーミング API を CLI 契約へ変換する小さなアダプター。

## 接続先（ホスト）の決め方

ローカルモデルを別の PC（GPU を積んだ 1 台）で走らせる構成は珍しくない。以前の唯一の
指定口は環境変数 `OLLAMA_HOST` だったが、agent-tools の実行はほとんどが**常駐体・
デーモン・GUI から起こされる子プロセス**で、そこへ環境変数を届けるには systemd unit や
シェル初期化を人が直す必要があった（dashboard の Windows→WSL 経路では、そもそも
Windows 側の環境変数が WSL へ渡らない）。「設定できない」わけではないが、置き場が
ツールごとにばらばらで、しかも画面から確かめられない。

そこで解決順を 1 つに決め、**このノードの宣言（host.yaml）**を最後の口として足す:

    --host <url>  >  $OLLAMA_HOST  >  host.yaml の `ollama.host`  >  http://127.0.0.1:11434

host.yaml（`~/.agents/agent-project.host.yaml`）は「このノードの資源とローカル環境」の
宣言で、`repos[]` のローカルクローンや `budget.max_concurrent` と同じ性質の値が既に
並んでいる。読み手は `agentcore.repolocal.load_host_declaration()` の 1 実装なので、
agent-project / agent-flow / agent-amigos / dashboard のどこから起動されても同じ答えになる
（探索順が実装ごとに増えると「どの宣言が効いているのか」がツールで変わる）。

    # ~/.agents/agent-project.host.yaml
    ollama:
      host: http://gpu-box.local:11434
      timeout_sec: 900

`ollama` ブロックは**このノードにしか無い接続先**なので、共有する状態リポジトリ側
（agent-project.yaml）には置かない（`repos[].local` と同じ理由）。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT_SEC = 600.0


def _declaration() -> dict:
    """このノードの宣言（host.yaml）の `ollama:` ブロック。読めなければ空 dict。

    宣言が読めないこと自体はエラーにしない——既定の接続先（localhost）へ倒れるだけで、
    失敗するなら接続時に「ollama に接続できません」として人に見える形で出る。
    """
    try:
        from .repolocal import load_host_declaration
    except ImportError:                     # 単体で取り出して使われた場合
        return {}
    raw = load_host_declaration().get("ollama")
    return raw if isinstance(raw, dict) else {}


def normalize_host(value: str) -> str:
    """`gpu-box:11434` のような scheme 省略も受ける。空文字なら空文字を返す。"""
    host = str(value or "").strip().rstrip("/")
    if not host:
        return ""
    return host if "://" in host else f"http://{host}"


def resolve_host(explicit: str = "", declaration: "dict | None" = None) -> str:
    """接続先の解決（--host > $OLLAMA_HOST > host.yaml > 既定）。"""
    decl = _declaration() if declaration is None else (declaration or {})
    for candidate in (explicit, os.environ.get("OLLAMA_HOST", ""), decl.get("host")):
        host = normalize_host(candidate)
        if host:
            return host
    return DEFAULT_HOST


def resolve_timeout_sec(declaration: "dict | None" = None) -> float:
    """HTTP 待ち上限。呼び出し側（agent-project 既定 300s 等）より短くしない。

    `OLLAMA_TIMEOUT` > host.yaml の `ollama.timeout_sec` > 600s。冷起動や大きめモデルで
    120s 打ち切りになると、外側の timeout より先にアダプタが落ちて誤って env 扱いになる。
    """
    decl = _declaration() if declaration is None else (declaration or {})
    for candidate in (os.environ.get("OLLAMA_TIMEOUT"), decl.get("timeout_sec")):
        if candidate is None or candidate == "":
            continue
        try:
            value = float(candidate)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return DEFAULT_TIMEOUT_SEC


def generate(model: str, prompt: str, host: str = "") -> dict:
    decl = _declaration()
    endpoint = resolve_host(host, decl)
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        f"{endpoint}/api/generate", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=resolve_timeout_sec(decl)) as res:
            data = json.load(res)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"ollama API error ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        # 接続先を必ず添える。既定の localhost だと思って見ている人と、host.yaml で
        # 別 PC を指しているノードとでは、次にやることが違う（サーバ起動 / 宛先の確認）。
        raise RuntimeError(f"ollama に接続できません（{endpoint}）: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"ollama API がタイムアウトしました（{endpoint}）") from exc
    if not isinstance(data, dict):
        raise RuntimeError("ollama API がオブジェクト以外を返しました")
    return data


def parse_args(args) -> "tuple[str, str]":
    """`<model> [--host <url>]` を (model, host) へ。不正なら ValueError。"""
    model = ""
    host = ""
    rest = list(args)
    while rest:
        token = rest.pop(0)
        if token == "--host":
            if not rest:
                raise ValueError("--host には接続先 URL が必要です")
            host = rest.pop(0)
        elif token.startswith("--host="):
            host = token[len("--host="):]
        elif token.startswith("-"):
            raise ValueError(f"未知のオプションです: {token}")
        elif model:
            raise ValueError(f"引数が多すぎます: {token}")
        else:
            model = token
    if not model:
        raise ValueError("モデル名が必要です")
    return model, host


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        model, host = parse_args(args)
    except ValueError as exc:
        print(f"{exc}\nusage: agent-ollama <model> [--host <url>]", file=sys.stderr)
        return 2
    try:
        result = generate(model, sys.stdin.read(), host)
        print(str(result.get("response") or ""), end="")
        print(
            f"@agent-usage tokens_in={int(result.get('prompt_eval_count') or 0)} "
            f"tokens_out={int(result.get('eval_count') or 0)}",
            file=sys.stderr)
        return 0
    except (RuntimeError, TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
