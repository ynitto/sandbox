from __future__ import annotations
# util.py — 元 agent-flow.py の 305-372 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# 小道具
# --------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# claim 用の厳密増加タイムスタンプ（旧 _unique_ts）と claim ロックのパス導出（旧
# _claim_lock_path）は agentcore.protocol へ移った（W0-8）。ここに複製を残すと、同じ
# claim_dir に対して 2 つのロック名前空間が並立し排他が効かなくなるため再定義しない。


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


def unwrap_list(data):
    """配列を求める契約で、配列 1 本を包んだオブジェクトを配列として受ける。

    ollama の JSON モード（`--format json`）は**トップレベルを必ずオブジェクトにする**ため、
    プロンプトで「配列だけを返せ」と書いても `{"data": [...]}` で返る（engine 側の仕様で
    モデルの能力ではない）。受け側が厳密なままだと split は原理的に契約を満たせず、形式修復
    リトライも必ず空振りして 1 回分の呼び出しを捨てる（C9・C10 — ローカルモデルを実用域に
    残し、無駄な再呼び出しを焼かない）。JSON モードを持つ他 CLI にも同じ形で効く。

    剥がすのは**配列値がちょうど 1 つ**のときだけ——2 つ以上あるとどれが答えか決まらず、
    黙って別のリストを採ると分解対象を取り違える。それ以外は素通しする。"""
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        if len(lists) == 1:
            return lists[0]
    return data


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """端末カラー等の ANSI エスケープを除去する。
    kiro-cli の出力にはカラーコードが混ざるため、保存・解析前に正規化する。"""
    return _ANSI_RE.sub("", text or "")

