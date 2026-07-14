from __future__ import annotations
# util.py — 元 agent-flow.py の 305-372 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# 小道具
# --------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_ts_lock = threading.Lock()
_last_ts = 0.0


def _unique_ts() -> float:
    """プロセス内で厳密に増加する claim 用タイムスタンプ。
    同値 ts による「決定的タイブレークの勝者」と「先着読みの勝者」の食い違い
    （同プロセスの並行 claim で二重勝者になりうる）を防ぐ。"""
    global _last_ts
    with _ts_lock:
        t = time.time()
        if t <= _last_ts:
            t = _last_ts + 1e-6
        _last_ts = t
        return t


def log(node: str, msg: str) -> None:
    print(f"[{now_iso()}] [{node}] {msg}", flush=True)


def read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_json_atomic(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def extract_json(text: str):
    """LLM 出力から JSON を寛容に取り出す（hermes-kiro-acp の作法）。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opn, cls in (("[", "]"), ("{", "}")):
        i, j = text.find(opn), text.rfind(cls)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("planner 出力から JSON を抽出できませんでした")


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """端末カラー等の ANSI エスケープを除去する。
    kiro-cli の出力にはカラーコードが混ざるため、保存・解析前に正規化する。"""
    return _ANSI_RE.sub("", text or "")

