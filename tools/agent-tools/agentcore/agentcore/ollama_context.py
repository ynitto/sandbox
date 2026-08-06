"""コンテキスト使用量の把握 — 上限の解決と、キャッシュ命中に強い使用量の追跡。

## なぜ要るか

ローカル推論の「不安定」の正体の 1 つが**文脈長の黙った切り捨て**である。会話が
`num_ctx` を超えると、ollama はエラーを返さず古い側を落とす——システムプロンプトが
消えた状態で答えが返るので、「たまに指示を無視する」という形でしか症状が出ない。

止まらないことを要件にしたバックアップ実行系（設計 §0.1 R1）では、これを
**見えるようにして、こちら側で先に手を打つ**のが筋になる。そのために、

1. 上限（`num_ctx`）を突き止め、
2. いまの使用量を追い、
3. 上限へ近づいたら警告し、足せるものを削り、それでも足りなければ**明示的に止める**

の 3 つをここに置く。サーバに黙って切り捨てさせない、が目的である。

## 使用量の測り方

チャットでは**直前の応答の `prompt_eval_count` が会話全体のトークン数**なので、
これ＋出力トークンが現在の使用量になる。ただしプレフィックスキャッシュが効いたとき、
版によっては「新しく評価した分」だけを返す。そこで、**会話は伸びる一方**という性質を
使って補正する（減って見えたら差分報告とみなし、積み上げる）。どちらの実装でも
正しく動き、どちらで測ったかは `context_source` で見分けられる。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

# 上限へ近づいたと見なす割合（既定）。ここを超えたら 1 回だけ警告する。
DEFAULT_WARN_PCT = 90.0

# 文字数 → トークン数の粗い換算。**サーバが数えてくれないときの保険**にだけ使う
# （日本語と英語が混ざる前提の丸い値。厳密さは要らない——用途は「あと何文字足せるか」の見積り）。
CHARS_PER_TOKEN = 3.0

# 次の応答のために空けておくトークン（生成の余地を潰さないため）。
DEFAULT_RESERVE_TOKENS = 512

# メタ情報（/api/ps・/api/show）の取得に許す秒数。**ここは短く**——コンテキスト表示の
# ために推論そのものを待たせるのは本末転倒だし、新しいハングの種にもしない。
_META_TIMEOUT_SEC = 3.0

_limit_cache: "dict[tuple, tuple[int, str]]" = {}


def estimate_tokens(text: str) -> int:
    return tokens_for_chars(len(text or ""))


def tokens_for_chars(chars: int) -> int:
    if chars <= 0:
        return 0
    return max(1, int(chars / CHARS_PER_TOKEN))


def _meta_timeout() -> float:
    try:
        value = float(os.environ.get("AGENT_OLLAMA_META_TIMEOUT", "") or _META_TIMEOUT_SEC)
    except (TypeError, ValueError):
        return _META_TIMEOUT_SEC
    return value if value > 0 else _META_TIMEOUT_SEC


def _host(host: str = "") -> str:
    """推論サーバの住所。実装は `ollama_loop.host_url()` の 1 つだけに保つ。

    循環 import を避けるため関数内で読む（`ollama_context` は下位の道具で、実行核へ
    依存を張りたくない——いまは一方向でも、逆向きが要る日に詰む）。
    """
    if host:
        return host.rstrip("/")
    from agentcore.ollama_loop import host_url
    return host_url()


def _get_json(host: str, path: str, body: "dict | None" = None) -> "dict | None":
    """メタ情報を 1 回問い合わせる。**失敗は None**（上限が分からないだけで実行は続く）。"""
    try:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{_host(host)}{path}", data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method="POST" if data is not None else "GET")
        with urllib.request.urlopen(req, timeout=_meta_timeout()) as res:
            parsed = json.load(res)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _from_ps(host: str, model: str) -> int:
    """読み込み済みモデルの実効文脈長（サーバが実際に確保した値。最も確か）。"""
    data = _get_json(host, "/api/ps")
    for entry in (data or {}).get("models") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("model") or "")
        if model and model not in (name, name.split(":")[0]):
            continue
        try:
            value = int(entry.get("context_length") or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return 0


def _from_show(host: str, model: str) -> int:
    """モデル自身が宣言する文脈長（`model_info` の `<arch>.context_length`）。"""
    data = _get_json(host, "/api/show", {"model": model})
    info = (data or {}).get("model_info")
    if not isinstance(info, dict):
        return 0
    for key, value in info.items():
        if str(key).endswith(".context_length"):
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
    return 0


def resolve_limit(model: str, *, options: "dict | None" = None, explicit: int = 0,
                  host: str = "", use_cache: bool = True) -> "tuple[int, str]":
    """実効の文脈長を (トークン数, どこから得たか) で返す。0 は「分からない」。

    優先順は「確かな順」ではなく**効く順**である。`num_ctx` を送っていればサーバは
    その値で確保するので宣言が勝ち、送っていなければサーバの実状（`/api/ps`）、
    それも取れなければモデルの宣言（`/api/show`）を見る。

    分からないままでも実行は続く（使用量は素のトークン数として表示するだけになる）。
    """
    explicit = int(explicit or 0)
    if explicit > 0:
        return explicit, "explicit"
    num_ctx = 0
    try:
        num_ctx = int((options or {}).get("num_ctx") or 0)
    except (TypeError, ValueError):
        num_ctx = 0
    if num_ctx > 0:
        return num_ctx, "options"

    key = (host, model)
    if use_cache and key in _limit_cache:
        return _limit_cache[key]

    value = _from_ps(host, model)
    source = "server"
    if value <= 0:
        value = _from_show(host, model)
        source = "model"
    if value <= 0:
        # **失敗はキャッシュしない**。TUI のような長命プロセスで一度取れなかっただけで、
        # 以後ずっと文脈表示が死ぬのは割に合わない（次の実行で取れるかもしれない）。
        return 0, "unknown"
    if use_cache:
        _limit_cache[key] = (value, source)
    return value, source


class ContextTracker:
    """いまの文脈使用量を追い、上限へ近づいたことを教える。

    `limit` が 0（不明）でも動く——その場合は使用量だけを報告し、警告と自衛は行わない
    （知らない上限を根拠に成果を削らない）。
    """

    def __init__(self, limit: int = 0, source: str = "unknown",
                 warn_pct: float = DEFAULT_WARN_PCT) -> None:
        self.limit = max(0, int(limit or 0))
        self.limit_source = source
        self.warn_pct = float(warn_pct)
        self.used = 0
        self.source = "unknown"
        self._pending_chars = 0
        self._warned = False

    # -- 入力側 -----------------------------------------------------------
    def add_text(self, text: str) -> None:
        """次の呼び出しの入力へ足したテキストを覚える（キャッシュ命中時の補正材料）。"""
        self._pending_chars += len(text or "")

    def observe(self, tokens_in: int, tokens_out: int) -> dict:
        """1 回の応答から使用量を更新する。

        `tokens_in + tokens_out` が前回より減って見えたら、それは会話が縮んだのではなく
        **サーバが新規評価分だけを報告した**ということ（プレフィックスキャッシュ命中）。
        会話は伸びる一方なので、その場合は積み上げに切り替える。
        """
        tokens_in = int(tokens_in or 0)
        tokens_out = int(tokens_out or 0)
        measured = tokens_in + tokens_out
        if tokens_in > 0 and measured >= self.used:
            self.used = measured
            self.source = "measured"
        else:
            self.used += tokens_out + tokens_for_chars(self._pending_chars)
            self.source = "estimated"
        self._pending_chars = 0
        return self.snapshot()

    # -- 出力側 -----------------------------------------------------------
    def pct(self) -> "float | None":
        if not self.limit:
            return None
        return round(self.used * 100.0 / self.limit, 1)

    def snapshot(self) -> dict:
        snap = {"context_used": self.used, "context_limit": self.limit,
                "context_source": self.source}
        pct = self.pct()
        if pct is not None:
            snap["context_pct"] = pct
        return snap

    def should_warn(self) -> bool:
        """閾値を跨いだ**最初の 1 回だけ**真（毎ラウンド同じ警告を出さない）。"""
        pct = self.pct()
        if pct is None or self._warned or pct < self.warn_pct:
            return False
        self._warned = True
        return True

    def remaining_tokens(self, reserve: int = DEFAULT_RESERVE_TOKENS) -> int:
        """次の入力に足せるトークン数。上限不明なら -1（制限しない）。"""
        if not self.limit:
            return -1
        pending = tokens_for_chars(self._pending_chars)
        return max(0, self.limit - self.used - pending - max(0, int(reserve)))

    def remaining_chars(self, reserve: int = DEFAULT_RESERVE_TOKENS) -> int:
        remaining = self.remaining_tokens(reserve)
        return -1 if remaining < 0 else int(remaining * CHARS_PER_TOKEN)
