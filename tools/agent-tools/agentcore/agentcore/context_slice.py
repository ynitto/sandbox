"""Python ソースを、必要なシンボルとその依存だけへ決定的に切り詰める。

なぜ切るのか: gemma4:e4b は長い文脈を**持てても引けない**（MRCR 128K 25.4）。
「入らないなら文脈を広げる」方向の対処は効かず、割るのが正解というのが実測の読みである
（`docs/plans/2026-08-14-agent-tools-local-first-operation-plan.md` §1.5）。read-only
agent が候補**ファイル**を絞る運用は既にあるが、1 ファイルが大きいとファイルの中で
同じ問題が起きる。ここはその内側——ファイルの中で見る範囲を機械が先に確定させる。

LLM は一切使わない。抽出は `ast` だけで決定的に行い、同じ入力には同じ抜粋が返る。
モデルに「関係するところを選ばせる」と、選別自体が見落としうる仕事になる。

守る規律が 2 つある。

1. **切れないときは切らない。** 構文が壊れている・対象が見つからないなら `None` を
   返し、呼び出し側はファイル全体へ倒す。中途半端な抜粋を黙って渡さない。
2. **省いたものを言う。** 抜粋の見出しへ、含めたシンボルと**省いたシンボル**を並べる。
   抜粋が「これで全部です」と読まれるのが、切り詰めで一番高くつく事故になる。

使い方（CLI）:

    python3 -m agentcore.context_slice eval/billing.py --symbol prorate

`--file` へ渡す編集対象ではなく、`--read` へ渡す**参照材料**を作る口である。
編集対象は原本をそのまま渡す（抜粋を編集させると行が合わない）。
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import NamedTuple, Sequence

HEADER_PREFIX = "# agentcore.context_slice"


class SliceResult(NamedTuple):
    """抜粋と、その抜粋が何を落としたか。

    `omitted` を戻り値に持たせているのは、呼び出し側が黙って捨てられないようにするため
    ——見出しへ書くのはこの値で、ここが空でないのに見出しへ出ない経路を作らない。
    """

    text: str
    included: "tuple[str, ...]"
    omitted: "tuple[str, ...]"
    kept_lines: int
    total_lines: int


class _Symbol(NamedTuple):
    name: str
    node: ast.AST
    start: int          # 1 始まり。デコレータを含む
    end: int            # 1 始まり・両端含む


def _span(node: ast.AST) -> "tuple[int, int]":
    """ソース上の範囲。デコレータは定義の一部として扱う。"""
    start = node.lineno
    for decorator in getattr(node, "decorator_list", ()) or ():
        start = min(start, decorator.lineno)
    return start, getattr(node, "end_lineno", node.lineno)


def _assigned_names(node: ast.AST) -> "list[str]":
    """モジュール直下の代入が作る名前。定数は関数と同じくらい依存として効く。"""
    if isinstance(node, ast.AnnAssign):
        return [node.target.id] if isinstance(node.target, ast.Name) else []
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    return []


def _module_symbols(module: ast.Module) -> "dict[str, _Symbol]":
    table: "dict[str, _Symbol]" = {}
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        else:
            names = _assigned_names(node)
        if not names:
            continue
        start, end = _span(node)
        for name in names:
            table[name] = _Symbol(name, node, start, end)
    return table


def _methods(cls: ast.ClassDef) -> "dict[str, _Symbol]":
    out: "dict[str, _Symbol]" = {}
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start, end = _span(node)
            out[node.name] = _Symbol(node.name, node, start, end)
    return out


def _referenced(node: ast.AST) -> "set[str]":
    """この定義が名前で触っているもの。属性は根の名前まで辿る。"""
    names: "set[str]" = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            base = child
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                names.add(base.id)
    return names


def _imports(module: ast.Module) -> "list[tuple[int, int]]":
    """モジュール直下の import は全部残す。

    依存だけへ絞ると「名前は出てくるのに import が無い」抜粋になり、読み手（人でも
    モデルでも）が最初に疑うのが抜粋の正しさになってしまう。import は安い。
    """
    spans = []
    for node in module.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            spans.append(_span(node))
    return spans


def _docstring_span(node: ast.AST) -> "tuple[int, int] | None":
    body = getattr(node, "body", None) or []
    first = body[0] if body else None
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        return _span(first)
    return None


def _comment_list(label: str, names: "Sequence[str]", width: int = 88) -> "list[str]":
    """見出しの一覧を折り返した `#` 行にする。1 行に詰めると読まれずに飛ばされる。"""
    lines, current = [], f"# {label}: "
    for i, name in enumerate(names):
        piece = name + (", " if i < len(names) - 1 else "")
        if len(current) + len(piece) > width and current.strip() != f"# {label}:":
            lines.append(current.rstrip())
            current = "#   "
        current += piece
    lines.append(current.rstrip())
    return lines


def _resolve(target: str, table: "dict[str, _Symbol]"
             ) -> "tuple[str, _Symbol, str | None] | None":
    """`func` / `Class` / `Class.method` を解決する。見つからなければ None。"""
    if target in table:
        return target, table[target], None
    if "." in target:
        head, _, method = target.partition(".")
        owner = table.get(head)
        if owner is not None and isinstance(owner.node, ast.ClassDef):
            found = _methods(owner.node).get(method)
            if found is not None:
                return target, owner, method
    return None


def _lines(source: str, span: "tuple[int, int]") -> "list[str]":
    text = source.splitlines()
    return text[span[0] - 1:span[1]]


def _render_partial_class(source: str, owner: _Symbol,
                          keep: "list[str]") -> "tuple[list[str], list[str]]":
    """クラスの外枠 + 選んだメソッドだけを描く。落としたメソッド名も返す。"""
    cls: ast.ClassDef = owner.node  # type: ignore[assignment]
    methods = _methods(cls)
    body_start = cls.body[0].lineno if cls.body else owner.end + 1
    header = _lines(source, (owner.start, body_start - 1))
    out = list(header)
    doc = _docstring_span(cls)
    if doc is not None:
        out += _lines(source, doc)
        out.append("")
    dropped = [name for name in methods if name not in keep]
    indent = " " * (cls.body[0].col_offset if cls.body else 4)
    if dropped:
        out.append(f"{indent}# … 省略（同じクラスに残っている）: " + ", ".join(dropped))
        out.append("")
    for name in sorted(keep, key=lambda n: methods[n].start):
        out += _lines(source, (methods[name].start, methods[name].end))
        out.append("")
    while out and not out[-1].strip():
        out.pop()
    return out, [f"{cls.name}.{name}" for name in dropped]


def slice_source(source: str, targets: "Sequence[str]", *,
                 path: "str | None" = None) -> "SliceResult | None":
    """`targets` とその依存だけを含む抜粋を返す。切れなければ None（呼び出し側は原本へ倒す）。

    依存はモジュール内に閉じたものだけを辿る——他モジュールまで展開すると、
    「1 ファイルの中で範囲を決める」道具が、勝手にリポジトリを歩く道具に変わる。
    """
    if not targets:
        return None
    try:
        module = ast.parse(source)
    except SyntaxError:
        return None
    table = _module_symbols(module)
    resolved = [r for r in (_resolve(t, table) for t in targets) if r is not None]
    if not resolved:
        return None

    # 依存の閉包。対象が触るモジュール内シンボルを、辿れなくなるまで足す。
    keep_top: "dict[str, _Symbol]" = {}
    class_methods: "dict[str, list[str]]" = {}
    queue: "list[_Symbol]" = []
    for _, symbol, method in resolved:
        if method is None:
            keep_top[symbol.name] = symbol
            queue.append(symbol)
        else:
            class_methods.setdefault(symbol.name, [])
            if method not in class_methods[symbol.name]:
                class_methods[symbol.name].append(method)
            queue.append(_methods(symbol.node)[method])  # type: ignore[arg-type]
    # クラス全体も同じクラスのメソッドも指名されたら、全体が勝つ。両方描くと同じ
    # クラスが 2 回出て、抜粋が原本に無い構造になる。
    for name in list(class_methods):
        if name in keep_top:
            del class_methods[name]
    seen = {s.name for s in queue}
    while queue:
        current = queue.pop()
        for name in sorted(_referenced(current.node)):
            found = table.get(name)
            if found is None or name in seen:
                continue
            seen.add(name)
            # 部分描画するクラスは丸ごとには足さない（メソッドを選んだ意味が消える）。
            if name in class_methods:
                continue
            keep_top[name] = found
            queue.append(found)

    spans = _imports(module)
    doc = _docstring_span(module)
    if doc is not None:
        spans.insert(0, doc)
    blocks: "list[tuple[int, int, list[str]]]" = [
        (s[0], s[1], _lines(source, s)) for s in spans]
    omitted: "list[str]" = []
    for symbol in keep_top.values():
        blocks.append((symbol.start, symbol.end,
                       _lines(source, (symbol.start, symbol.end))))
    for name, keep in class_methods.items():
        owner = table[name]
        body, dropped = _render_partial_class(source, owner, keep)
        blocks.append((owner.start, owner.end, body))
        omitted += dropped
    blocks.sort(key=lambda b: b[0])

    included = sorted(keep_top) + sorted(
        f"{cls}.{m}" for cls, ms in class_methods.items() for m in ms)
    dropped_top = [name for name in table
                   if name not in keep_top and name not in class_methods]
    omitted = sorted(omitted + dropped_top)

    body_lines: "list[str]" = []
    previous_end = 0
    for start, end, block in blocks:
        # 原本で隣り合っていたものは隣り合わせのまま出す（連続する import が 1 行ずつ
        # ばらけると、抜粋が原本と違う構造に見える）。離れていたものの間だけ空ける。
        if body_lines and start > previous_end + 1:
            body_lines.append("")
        body_lines += block
        previous_end = end
    total = len(source.splitlines())
    kept = len(body_lines)
    head = [f"{HEADER_PREFIX}: {path or '(source)'} の抜粋（{kept}/{total} 行）"]
    head += _comment_list("含めたシンボル", included or ["(なし)"])
    if omitted:
        # 省略を必ず見出しへ出す。ここが空でない抜粋を「全部」と読まれると、
        # 見落としが抜粋のせいなのかモデルのせいなのか、後から切り分けられない。
        head += _comment_list("省略したシンボル（同じファイルに残っている）", omitted)
    text = "\n".join(head + [""] + body_lines) + "\n"
    return SliceResult(text, tuple(included), tuple(omitted), kept, total)


def slice_file(path: "str | Path", targets: "Sequence[str]") -> "SliceResult | None":
    try:
        source = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return slice_source(source, targets, path=str(path))


def main(argv: "list[str] | None" = None) -> int:
    """抜粋を stdout へ。切れなければ**原本をそのまま**出す（fail open）。

    パイプの途中に置く道具なので、失敗しても後段へ材料が届く側へ倒す。何が起きたかは
    stderr に必ず出す——静かに原本へ倒れると、抜粋が効いていない条件で測ってしまう。
    """
    ap = argparse.ArgumentParser(
        prog="agentcore.context_slice",
        description="Python ソースから対象シンボルとその依存だけを決定的に抜き出す")
    ap.add_argument("path", help="対象の .py")
    ap.add_argument("--symbol", action="append", dest="symbols", required=True,
                    metavar="NAME", help="残すシンボル（func / Class / Class.method）。複数可")
    ap.add_argument("-o", "--out", help="書き出し先（既定は stdout）")
    args = ap.parse_args(argv)

    try:
        source = Path(args.path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"agent-slice: 読めません: {exc}", file=sys.stderr)
        return 2
    result = slice_source(source, args.symbols, path=args.path)
    if result is None:
        print(f"agent-slice: {args.path} を切れませんでした"
              f"（構文が壊れているか、{', '.join(args.symbols)} が見つからない）。"
              "ファイル全体を出します", file=sys.stderr)
        text = source
    else:
        text = result.text
        print(f"agent-slice: {args.path} {result.kept_lines}/{result.total_lines} 行 / "
              f"省略 {len(result.omitted)} シンボル", file=sys.stderr)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
