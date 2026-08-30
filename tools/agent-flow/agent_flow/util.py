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


from agentcore import llmjson as _llmjson

def extract_json(text: str):
    """LLM 出力から JSON を寛容に取り出す。実装は :mod:`agentcore.llmjson`（1 実装）。

    寛容さの規則が engine ごとにずれると、同じモデル応答が経路によって通ったり落ちたり
    する——しかも落ちた側は「モデルが悪い」に見えるので原因が分からない。だから写しを
    持たない（C7）。メッセージだけ planner 文脈のものにする。
    """
    return _llmjson.extract_json(text, what="planner 出力")


def unwrap_list(data):
    """配列を包んだ器を剥がす。実装は :mod:`agentcore.llmjson`（1 実装）。

    ollama の JSON モードがトップレベルをオブジェクトに固定するため必要になる手当て。
    """
    return _llmjson.unwrap_list(data)


def extract_list(text: str):
    """split の文字列配列を抽出し、文字列配列のグループ表現だけを正規化する。

    Thinking を使うモデルは、正しい複数グループを
    ``["a", "b"], ["c", "d"]`` のように外側の配列なしで返すことがある。この形は
    各グループと要素が一意なので、再推論せず ``["a,b", "c,d"]`` へ畳める。
    数値・object・混在配列は意味が決まらないため受理せず、呼び出し側の形式修復へ回す。"""
    # グループ表現（外側の配列なしで並ぶ文字列配列）は**先に**畳む。共有の JSON 抽出は
    # 連結された JSON を走査して最後の器を返すので（制御封筒対策・agentcore.llmjson）、
    # ここを後回しにすると `["a","b"], ["c","d"]` が最後の 1 グループだけになる。
    try:
        grouped = json.loads(f"[{text.strip()}]")
    except (json.JSONDecodeError, ValueError):
        grouped = None
    if (isinstance(grouped, list) and len(grouped) > 1
            and all(isinstance(g, list) and g and all(isinstance(i, str) for i in g)
                    for g in grouped)):
        return [",".join(group) for group in grouped]
    try:
        data = unwrap_list(extract_json(text))
    except (ValueError, json.JSONDecodeError) as original:
        try:
            data = json.loads(f"[{text}]")
        except json.JSONDecodeError:
            raise original
    if isinstance(data, list) and all(isinstance(item, str) for item in data):
        return data
    if (isinstance(data, list) and data
            and all(isinstance(group, list) and group
                    and all(isinstance(item, str) for item in group)
                    for group in data)):
        return [",".join(group) for group in data]
    raise ValueError("split 出力は文字列の JSON 配列ではありません")


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """端末カラー等の ANSI エスケープを除去する。
    kiro-cli の出力にはカラーコードが混ざるため、保存・解析前に正規化する。"""
    return _ANSI_RE.sub("", text or "")
