#!/usr/bin/env python3
"""agent-opencode — opencode を agents/<name>.json 契約へ合わせる薄いアダプター。

## なぜ素の `opencode run` を直接呼ばないか

3 つとも「素の argv では表せない」ものなので、CLI 定義（agents/opencode.json）ではなく
ここに置いている。

1. **実測 usage**。`opencode run --format json` は `step_finish` イベントに
   `tokens.{input,output}` を載せる。これを stderr の `@agent-usage` 1 行へ移すと、
   台帳（agent-audit の measured 集計）が推定ではなく実測で埋まる。ollama の
   `agent-ollama` が同じ理由で居るのと同型。
2. **落ちない失敗を落とす**。推論サーバ（別 PC の ollama）が落ちていると opencode は
   即座に失敗せず**内部リトライで待ち続ける**（実測: 接続不可のまま 120 秒待っても
   終了しない）。呼び出し側のタイムアウトまで枠を焼くだけなので、実行前に到達性を
   1 回だけ確かめ、駄目なら env 分類に乗るメッセージで即座に失敗する。
3. **本文だけを stdout へ**。`--format json` の生イベントは本文ではないので、
   text パートだけを取り出して stdout に出す（イベントログは stderr へ素通し）。

## 契約

- 引数はそのまま `opencode run --format json <引数…>` へ渡す（`--model` 等は CLI 定義が組む）
- プロンプトは stdin。そのまま opencode の stdin へ流す（opencode は非 TTY の stdin を
  メッセージとして読む）
- stdout = 最終応答の本文 / stderr = opencode の診断 + `@agent-usage tokens_in=… tokens_out=…`
- 終了コードは opencode のものをそのまま返す

標準ライブラリのみ（pip 依存なし）。
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USAGE = """使い方: agent-opencode [--check] [--no-preflight] [opencode run のオプション…]

  プロンプトは stdin から渡す。引数は `opencode run --format json` へそのまま渡る。

  --check          推論サーバへの到達性だけを確かめて終わる（LLM を呼ばない）
  --no-preflight   実行前の到達性チェックをしない
  -h, --help       このヘルプ

  環境変数:
    OPENCODE_BIN                  opencode の実行ファイル（既定 opencode）
    AGENT_OPENCODE_PROVIDER       モデル名に provider/ が無いときの既定プロバイダ（既定 ollama）
    AGENT_OPENCODE_SKIP_PREFLIGHT 1 で到達性チェックを省く
    AGENT_OPENCODE_PREFLIGHT_TIMEOUT  到達性チェックの秒数（既定 5）

  OLLAMA_HOST / OLLAMA_API_BASE / NO_PROXY が揃っていなければ ~/.profile から補完し、
  ollama のホストを NO_PROXY / no_proxy へ追記してプロキシを迂回する
  （非ログインシェル起動で環境変数が届かない場合の救済。agent-ollama と同じ挙動）
"""

_COMMENT_RE = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")
_ENV_TEMPLATE_RE = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


# ---------------------------------------------------------------------------
# 環境の補完（非ログインシェル対策）
#
# 実装は agentcore.hostenv が唯一持つ（複製しない — C7）。opencode は推論サーバ（別 PC の
# ollama）へ OLLAMA_API_BASE で向かうので、補完が無いと既定の localhost を見るか、接続が
# プロキシへ流れて 504 になる。以下は呼び出し側の綴りを変えないための再輸出。
# ---------------------------------------------------------------------------
from agentcore.hostenv import (  # noqa: F401  (再輸出)
    _PROFILE_ENV_EXACT,
    _PROFILE_ENV_PREFIXES,
    _complete_ollama_env,
    _import_profile_env,
    load_profile_env,
)


def _strip_jsonc(text: str) -> str:
    """opencode の設定は .jsonc も許すのでコメントと末尾カンマを落とす。

    文字列リテラルを先に食う 1 本の正規表現なので、`"http://x"` の `//` は消えない。
    """
    def repl(m: "re.Match[str]") -> str:
        tok = m.group(0)
        return tok if tok.startswith('"') else ""
    return _TRAILING_COMMA_RE.sub(r"\1", _COMMENT_RE.sub(repl, text))


def _read_config(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(_strip_jsonc(raw))
    except ValueError:
        # 壊れた設定は opencode 自身が「Configuration is invalid」で落ちる。
        # ここで別の言い方の失敗を作らない（到達性チェックを黙って諦めるだけ）。
        return {}
    return data if isinstance(data, dict) else {}


def _config_candidates() -> "list[Path]":
    """opencode が読む設定の所在（グローバル → プロジェクト）。

    プロジェクト側は cwd から上へ辿る。opencode 本体の探索規則と厳密に同じである必要は
    ない——ここで欲しいのは baseURL だけで、外したら到達性チェックを省くだけだから。
    """
    paths: "list[Path]" = []
    explicit = os.environ.get("OPENCODE_CONFIG")
    if explicit:
        return [Path(explicit).expanduser()]
    cfg_dir = os.environ.get("OPENCODE_CONFIG_DIR")
    home_dir = Path(cfg_dir).expanduser() if cfg_dir else Path.home() / ".config" / "opencode"
    for name in ("opencode.json", "opencode.jsonc"):
        paths.append(home_dir / name)
    here = Path.cwd().resolve()
    for d in [here, *here.parents]:
        for name in ("opencode.json", "opencode.jsonc"):
            paths.append(d / name)
    return paths


def load_providers() -> "tuple[dict, str]":
    """設定から provider 定義と既定モデルを集める（後に読んだ方＝プロジェクト側が勝つ）。"""
    providers: dict = {}
    default_model = ""
    for path in _config_candidates():
        if not path.is_file():
            continue
        data = _read_config(path)
        prov = data.get("provider")
        if isinstance(prov, dict):
            for key, value in prov.items():
                if isinstance(value, dict):
                    providers[str(key)] = value
        model = data.get("model")
        if isinstance(model, str) and model:
            default_model = model
    return providers, default_model


def _expand_env(value: str) -> str:
    return _ENV_TEMPLATE_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)


def provider_of(args: "list[str]", default_model: str) -> str:
    """argv の `--model provider/model` からプロバイダ名を取る（無ければ設定の既定）。"""
    model = ""
    for i, tok in enumerate(args):
        if tok in ("-m", "--model") and i + 1 < len(args):
            model = args[i + 1]
        elif tok.startswith("--model="):
            model = tok.split("=", 1)[1]
    model = model or default_model
    if "/" in model:
        return model.split("/", 1)[0]
    return os.environ.get("AGENT_OPENCODE_PROVIDER", "ollama")


def base_url_of(providers: dict, provider: str) -> str:
    entry = providers.get(provider)
    if not isinstance(entry, dict):
        return ""
    options = entry.get("options")
    if not isinstance(options, dict):
        return ""
    url = options.get("baseURL") or options.get("baseUrl") or ""
    return _expand_env(str(url)).rstrip("/")


def _preflight_timeout() -> float:
    raw = os.environ.get("AGENT_OPENCODE_PREFLIGHT_TIMEOUT", "5")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 5.0
    return value if value > 0 else 5.0


def reachable(base_url: str) -> "tuple[bool, str]":
    """OpenAI 互換の `GET <baseURL>/models` を叩いて到達性だけを見る。

    401/404 でも「サーバは居る」なので成功扱い——ここで見たいのは認証やモデルの有無では
    なく、**接続できるかどうか**（できないと opencode が待ち続ける）だけ。
    """
    req = urllib.request.Request(f"{base_url}/models", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_preflight_timeout()) as res:
            res.read(1)
        return True, ""
    except urllib.error.HTTPError as exc:
        return True, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return False, str(exc.reason)
    except (TimeoutError, OSError) as exc:
        return False, str(exc)


def preflight(args: "list[str]") -> "tuple[bool, str]":
    """(続行してよいか, 人向けメッセージ)。設定を読めないときは黙って続行する。"""
    providers, default_model = load_providers()
    provider = provider_of(args, default_model)
    base_url = base_url_of(providers, provider)
    if not base_url:
        # クラウドのプロバイダ（baseURL を持たない）や設定未検出。opencode に任せる。
        return True, f"プロバイダ {provider} に baseURL の宣言がないため到達性チェックを省きます"
    ok, detail = reachable(base_url)
    if ok:
        return True, f"推論サーバに到達しました: {base_url}{f' ({detail})' if detail else ''}"
    return False, (f"推論サーバに接続できません: {base_url} ({detail})\n"
                   f"  provider={provider} — 別 PC の ollama なら、そちらで "
                   "`OLLAMA_HOST=0.0.0.0 ollama serve` が動いているか、"
                   "ポート 11434 がファイアウォールで閉じていないかを確認してください。")


def _emit(parts: "dict[str, str]", tokens_in: int, tokens_out: int) -> None:
    body = "\n\n".join(t for t in parts.values() if t)
    sys.stdout.write(body + ("\n" if body and not body.endswith("\n") else ""))
    sys.stdout.flush()
    # 実測 usage は stderr の独立した 1 行（schemas/agent-cli.schema.json の契約）。
    # 本文（stdout）には混ぜない——混ぜると成果物にゴミが載る。
    print(f"@agent-usage tokens_in={tokens_in} tokens_out={tokens_out}", file=sys.stderr)


def run(args: "list[str]", prompt: str) -> int:
    binary = os.environ.get("OPENCODE_BIN", "opencode")
    argv = [binary, "run", "--format", "json", *args]
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    except FileNotFoundError:
        print(f"opencode が見つかりません: {binary}"
              "（tools/opencode/install.sh でインストールしてください）", file=sys.stderr)
        return 127

    # 呼び出し側（agent-project 等）がタイムアウトで殺しに来たとき、opencode を残さない。
    def _forward(signum, _frame):
        try:
            proc.send_signal(signum)
        except (ProcessLookupError, ValueError):
            pass
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _forward)
        except (ValueError, OSError):
            pass

    try:
        proc.stdin.write(prompt)
    except BrokenPipeError:
        pass
    finally:
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    parts: "dict[str, str]" = {}
    tokens_in = tokens_out = 0
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            # JSON で来なかった行は本文ではない（進捗表示など）。stderr へ逃がす。
            print(line, file=sys.stderr)
            continue
        if not isinstance(event, dict):
            continue
        kind = str(event.get("type") or "")
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        if kind == "text":
            # 同じ part.id が複数回来る実装（逐次更新）でも最後の全文だけが残るよう、
            # id をキーにして上書きする。初出順は dict が保つ。
            parts[str(part.get("id") or len(parts))] = str(part.get("text") or "")
        elif kind == "step_finish":
            tokens = part.get("tokens")
            if isinstance(tokens, dict):
                tokens_in += int(tokens.get("input") or 0)
                tokens_out += int(tokens.get("output") or 0)
        elif kind == "error":
            print(json.dumps(event.get("error") or event, ensure_ascii=False), file=sys.stderr)

    rc = proc.wait()
    _emit(parts, tokens_in, tokens_out)
    return rc


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "-h" in args or "--help" in args:
        print(USAGE)
        return 0
    check_only = "--check" in args
    skip = ("--no-preflight" in args
            or os.environ.get("AGENT_OPENCODE_SKIP_PREFLIGHT", "") == "1")
    args = [a for a in args if a not in ("--check", "--no-preflight")]

    # 到達性チェックと opencode の起動より前に環境を補完する
    # （設定の {env:...} 展開・プロキシ迂回の両方に効く）。
    load_profile_env()

    if check_only or not skip:
        ok, message = preflight(args)
        if not ok:
            print(message, file=sys.stderr)
            return 1
        if check_only:
            print(message)
            return 0

    return run(args, sys.stdin.read())


if __name__ == "__main__":
    raise SystemExit(main())
