"""JSON / YAML を読み、形の違うテストケースを同じ Case にそろえる。

- JSON も YAML の読み手で読む（JSON はほぼ YAML なので、行番号と重複キーの検出を共有できる）
- LLM が出しがちな ``` の囲みは、行番号を保ったまま中身だけを読む
- 項目名は英語・日本語・Gherkin（given / when / then）のよくある綴りを受ける。設定で足せる
- 手順の中の `expect:` などは期待結果として扱う（webui-test のケースファイルをそのまま読める）
- 知らない項目（mocks・localStorage・テストデータなど）は前提として扱う。
  同じ手順でも前提が違えば別のケースとして比べるため
"""

from __future__ import annotations

import json
import re

import yaml

from .model import Case, Finding, Item

FIELD_ALIASES = {
    "id": ["id", "case_id", "caseid", "test_id", "testid", "tc_id", "no", "number", "key",
           "番号", "項番", "no.", "テストid", "ケースid", "テスト番号", "ケース番号"],
    "title": ["title", "name", "summary", "scenario", "scenario_name", "test_name", "testname", "objective",
              "item", "check", "checkpoint", "タイトル", "テスト名", "ケース名", "テスト項目", "確認項目",
              "項目", "概要", "シナリオ", "目的", "チェック項目"],
    "description": ["description", "desc", "説明", "内容", "詳細"],
    "preconditions": ["precondition", "preconditions", "pre_condition", "pre_conditions", "prerequisite",
                      "prerequisites", "given", "setup", "background", "前提", "前提条件", "事前条件",
                      "事前準備", "準備"],
    "steps": ["steps", "step", "procedure", "procedures", "actions", "action", "when", "test_steps",
              "手順", "操作", "操作手順", "テスト手順", "実施手順"],
    "expected": ["expected", "expected_result", "expected_results", "expectation", "expectations",
                 "then", "assert", "asserts", "assertion", "assertions", "verify", "verification",
                 "result", "期待結果", "期待値", "確認内容", "確認結果", "合否基準", "判定基準", "期待する結果"],
    "requirements": ["requirement", "requirements", "req", "reqs", "requirement_id", "requirement_ids",
                     "refs", "ref", "trace", "traceability", "story", "要件", "要件id", "要求", "関連要件"],
}
# 比較に使わない付帯情報（前提にも入れない）
META_KEYS = {"tags", "tag", "priority", "severity", "status", "author", "owner", "notes", "note",
             "comment", "comments", "category", "type", "level", "component", "module", "automated",
             "estimate", "created", "updated", "version", "タグ", "優先度", "重要度", "状態", "作成者",
             "担当", "担当者", "備考", "メモ", "コメント", "区分", "分類", "種別", "カテゴリ", "機能",
             "画面", "自動化"}
# 手順の中で期待結果を表すキー
STEP_EXPECT_KEYS = {"expect", "expected", "expected_result", "assert", "verify", "check", "then",
                    "期待結果", "期待値", "確認", "確認内容"}
STEP_ACTION_KEYS = {"action", "step", "do", "operation", "when", "操作", "手順", "内容"}
# 入れ子のまとまり（suite・feature など）の名前に使うキー
GROUP_NAME_KEYS = ("suite", "feature", "name", "title", "group", "section", "機能", "画面", "名前", "タイトル")

_FENCE = re.compile(r"^\s*(```|~~~)")
_NUMBERED = re.compile(r"^\s*(?:\d+[.)．、]|[-*・●]|\(\d+\)|（\d+）|[①-⑳])\s*")


class _Str(str):
    line = 0


class _Map(dict):
    line = 0


class _List(list):
    line = 0


class _Loader(yaml.SafeLoader):
    pass


def _construct_mapping(loader, node):
    loader.flatten_mapping(node)
    m = _Map()
    m.line = node.start_mark.line + 1
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            hash(key)
        except TypeError:
            key = str(key)
        if key in m:
            loader.dup_keys.append((str(key), key_node.start_mark.line + 1))
        m[key] = loader.construct_object(value_node, deep=True)
    return m


def _construct_seq(loader, node):
    s = _List(loader.construct_object(n, deep=True) for n in node.value)
    s.line = node.start_mark.line + 1
    return s


def _construct_str(loader, node):
    s = _Str(loader.construct_scalar(node))
    s.line = node.start_mark.line + 1
    return s


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)
_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_SEQUENCE_TAG, _construct_seq)
_Loader.add_constructor("tag:yaml.org,2002:str", _construct_str)


def _line(x, default=0):
    return getattr(x, "line", 0) or default


def strip_fences(text: str) -> str:
    """``` で囲まれた部分があれば、その外側を空行に置き換える（行番号は変えない）。"""
    lines = text.split("\n")
    marks = [i for i, ln in enumerate(lines) if _FENCE.match(ln)]
    if len(marks) < 2:
        return text
    blocks = list(zip(marks[0::2], marks[1::2]))
    keep = set()
    for a, b in blocks:
        keep.update(range(a + 1, b))
    return "\n".join(ln if i in keep else "" for i, ln in enumerate(lines))


def parse_text(text: str):
    """文書の列と重複キーの一覧を返す。読めなければ yaml.YAMLError / ValueError。"""
    text = strip_fences(text.lstrip("﻿"))
    loader = _Loader(text)
    loader.dup_keys = []
    docs = []
    try:
        while loader.check_data():
            docs.append(loader.get_data())
    finally:
        loader.dispose()
    if not docs:
        # YAML として読めない JSON の方言はまず無いが、念のため素の JSON でも試す
        docs = [json.loads(text)] if text.strip() else []
    return docs, loader.dup_keys


class FieldMap:
    def __init__(self, extra=None):
        self.alias = {}
        for canon, names in FIELD_ALIASES.items():
            for n in [*names, *((extra or {}).get(canon) or [])]:
                self.alias[self._norm(n)] = canon

    @staticmethod
    def _norm(k) -> str:
        return re.sub(r"[\s\-]+", "_", str(k).strip().lower())

    def canon(self, key):
        return self.alias.get(self._norm(key))

    def is_meta(self, key) -> bool:
        return self._norm(key) in META_KEYS


def _scalar_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v).strip()


def flatten(v) -> str:
    """構造のある値を、比較できる 1 行の文にする。文字列の値は "" で囲み、値として扱わせる。"""
    if isinstance(v, dict):
        return " ".join(f"{k}={flatten(x)}" if isinstance(x, (dict, list)) else f'{k}="{_scalar_text(x)}"'
                        for k, x in v.items())
    if isinstance(v, list):
        return "[" + ", ".join(flatten(x) for x in v) + "]"
    return f'"{_scalar_text(v)}"'


def _split_lines(text, line, field):
    """複数行の文字列を、番号付き・箇条書きの行ごとに分ける。"""
    out = []
    parts = str(text).split("\n")
    for k, p in enumerate(parts):
        p = _NUMBERED.sub("", p).strip()
        if p:
            out.append(Item(p, line + k if line else 0, f"{field}[{len(out)}]"))
    return out


def _items(value, field, base_line):
    """前提・期待結果の値を Item の列にする。"""
    line = _line(value, base_line)
    if value is None:
        return []
    if isinstance(value, list):
        out = []
        for x in value:
            ln = _line(x, line)
            if isinstance(x, (dict, list)):
                t = flatten(x)
                if t:
                    out.append(Item(t, ln, f"{field}[{len(out)}]"))
            else:
                t = _NUMBERED.sub("", _scalar_text(x)).strip()
                if t:
                    out.append(Item(t, ln, f"{field}[{len(out)}]"))
        return out
    if isinstance(value, dict):
        return [Item(f"{k}: {_scalar_text(v) if not isinstance(v, (dict, list)) else flatten(v)}",
                     _line(v, line), f"{field}.{k}") for k, v in value.items()]
    text = _scalar_text(value)
    # ブロック（| や >）の中身は、キーの次の行から始まる
    return _split_lines(text, line + 1 if line and "\n" in text else line, field)


def _steps(value, base_line, fmap):
    """手順を (手順, 手順に書かれた期待結果) に分ける。"""
    steps, expected = [], []
    if value is None:
        return steps, expected
    if not isinstance(value, list):
        return _items(value, "steps", base_line), expected
    for i, s in enumerate(value):
        ln = _line(s, _line(value, base_line))
        if isinstance(s, dict):
            act, exp, rest = None, None, {}
            for k, v in s.items():
                key = FieldMap._norm(k)
                if key in STEP_EXPECT_KEYS or fmap.canon(k) == "expected":
                    exp = v
                elif key in STEP_ACTION_KEYS or fmap.canon(k) == "steps":
                    act = v
                else:
                    rest[k] = v
            if act is not None or rest:
                parts = []
                if act is not None:
                    parts.append(_scalar_text(act) if not isinstance(act, (dict, list)) else flatten(act))
                if rest:
                    parts.append(" ".join(f"{k} {flatten(v)}" for k, v in rest.items()))
                steps.append(Item(" ".join(p for p in parts if p), ln, f"steps[{i}]"))
            if exp is not None:
                for it in _items(exp if isinstance(exp, list) else [exp], f"steps[{i}].expected", ln):
                    if isinstance(exp, dict):
                        it.text = flatten(exp)
                    expected.append(it)
                    if isinstance(exp, dict):
                        break
        else:
            t = _NUMBERED.sub("", _scalar_text(s)).strip()
            if t:
                steps.append(Item(t, ln, f"steps[{i}]"))
    return steps, expected


def _requirements(value) -> list:
    if value is None:
        return []
    vals = value if isinstance(value, list) else re.split(r"[,、，\s]+", str(value))
    return [str(v).strip() for v in vals if str(v).strip()]


def _looks_like_case(d, fmap, loose=True) -> bool:
    """ケースらしいか。loose はケースの列と分かっている所（cases: など）で使い、タイトルだけでもケースとみなす。"""
    keys = {fmap.canon(k) for k in d}
    if keys & {"steps", "expected", "preconditions"}:
        return True
    if loose:
        return "title" in keys or ("id" in keys and len(d) > 1)
    return False


# ケースの列を置くキー。これがあれば、隣のキー（variants・mocks・setup など）は読まない
CASE_LIST_KEYS = {"checklist", "checks", "items", "check_items", "checkpoints", "cases", "testcases",
                  "test_cases", "tests", "scenarios", "specs", "チェックリスト", "確認項目", "チェック項目", "項目",
                  "テストケース", "ケース", "シナリオ", "テスト項目"}


def _case_list_keys(d):
    return [k for k, v in d.items() if FieldMap._norm(k) in CASE_LIST_KEYS and isinstance(v, list)]


def _make_case(d, file, group, index, kind, fmap):
    c = Case(file=file, line=_line(d), index=index, group=tuple(group), kind=kind)
    extra_pre = []
    desc = ""
    for k, v in d.items():
        canon = fmap.canon(k)
        if canon == "id":
            c.id = _scalar_text(v) or None
        elif canon == "title":
            c.title = _scalar_text(v) if not isinstance(v, (dict, list)) else flatten(v)
        elif canon == "description":
            desc = _scalar_text(v) if not isinstance(v, (dict, list)) else flatten(v)
        elif canon == "preconditions":
            c.preconditions += _items(v, "preconditions", c.line)
        elif canon == "steps":
            s, e = _steps(v, c.line, fmap)
            c.steps += s
            c.expected += e
        elif canon == "expected":
            c.expected += _items(v, "expected", c.line)
        elif canon == "requirements":
            c.requirements += _requirements(v)
        elif fmap.is_meta(k):
            continue
        else:
            if v is not None and v != "" and v != [] and v != {}:
                extra_pre.append(Item(f"{k}: {flatten(v) if isinstance(v, (dict, list)) else _scalar_text(v)}",
                                      _line(v, c.line), str(k)))
    c.preconditions += extra_pre
    if not c.title and desc:
        c.title = desc
    return c


def extract_cases(doc, file, fmap, start_index=0):
    """読み込んだ木からケースを探す。返り値は Case の列。"""
    cases = []

    def add_list(lst, group, known):
        dicts = [x for x in lst if isinstance(x, dict)]
        # 1 件も手順・期待結果を持たない列はチェックリストとみなす
        has_body = any({fmap.canon(k) for k in x} & {"steps", "expected", "preconditions"} for x in dicts)
        kind = "case" if has_body else "check"
        for x in lst:
            if isinstance(x, dict):
                if _case_list_keys(x) and not {fmap.canon(k) for k in x} & {"steps", "expected"}:
                    walk(x, group)
                elif _looks_like_case(x, fmap, loose=known):
                    cases.append(_make_case(x, file, group, start_index + len(cases), kind, fmap))
                else:
                    walk(x, group)
            elif known and isinstance(x, (str, int, float)) and _scalar_text(x):
                cases.append(Case(file=file, line=_line(x, _line(lst)), index=start_index + len(cases),
                                  group=tuple(group), title=_scalar_text(x), kind="check"))
            elif isinstance(x, list):
                add_list(x, group, known)

    def walk(node, group, known=False):
        if isinstance(node, list):
            add_list(node, group, known)
        elif isinstance(node, dict):
            keys = _case_list_keys(node)
            if keys:
                name = next((_scalar_text(node[k]) for k in node if FieldMap._norm(k) in GROUP_NAME_KEYS
                             and isinstance(node[k], (str, int))), "")
                g = [*group, name] if name else list(group)
                for k in keys:
                    add_list(node[k], g, True)
            elif _looks_like_case(node, fmap, loose=False) and not group and not cases:
                cases.append(_make_case(node, file, group, start_index, "case", fmap))
            else:
                for k, v in node.items():
                    if isinstance(v, (list, dict)) and fmap.canon(k) is None and not fmap.is_meta(k):
                        walk(v, [*group, str(k)])

    walk(doc, [], known=isinstance(doc, list))
    return cases


def load_file(path, display, fmap, start_index=0):
    """1 ファイルを読み、(ケースの列, ファイル自体の指摘) を返す。"""
    findings = []
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return [], [Finding("D00", f"読めません: {e}", file=display, line=0)]
    try:
        docs, dup_keys = parse_text(text)
    except (yaml.YAMLError, ValueError) as e:
        mark = getattr(e, "problem_mark", None)
        line = mark.line + 1 if mark else getattr(e, "lineno", 0)
        msg = str(e).split("\n")[0]
        return [], [Finding("D00", f"JSON / YAML として読めません: {msg}", file=display, line=line,
                            hint="括弧・引用符・インデントの崩れを直す")]
    for key, line in dup_keys:
        findings.append(Finding("D15", f"キー「{key}」が同じ所に 2 回あり、先の値は読まれずに捨てられます",
                                file=display, line=line, hint="どちらかを消すか、別のキーにする"))
    cases = []
    for doc in docs:
        cases += extract_cases(doc, display, fmap, start_index + len(cases))
    return cases, findings


def load_requirements(path, fmap):
    """要件一覧を ({id: 本文}, {id: 行番号}) で返す。

    受ける形: ID の列 / {id, title|text|description} の列 / {ID: 本文} の dict /
    それらを requirements・items などのキーの下に置いたもの。
    """
    with open(path, encoding="utf-8") as f:
        docs, _ = parse_text(f.read())
    reqs, lines = {}, {}

    def take(node):
        if isinstance(node, list):
            for x in node:
                if isinstance(x, dict):
                    rid = next((_scalar_text(v) for k, v in x.items() if fmap.canon(k) == "id"), "")
                    if rid:
                        text = " ".join(_scalar_text(v) for k, v in x.items()
                                        if fmap.canon(k) in ("title", "description", "expected")
                                        or FieldMap._norm(k) in ("text", "statement", "本文", "要件"))
                        reqs[rid] = text
                        lines[rid] = _line(x)
                    else:
                        take(list(x.values()))
                elif isinstance(x, (str, int)):
                    reqs[_scalar_text(x)] = ""
                    lines[_scalar_text(x)] = _line(x)
                elif isinstance(x, list):
                    take(x)
        elif isinstance(node, dict):
            if all(isinstance(v, (str, int, float)) or v is None for v in node.values()) and node and \
                    not any(fmap.canon(k) == "id" for k in node):
                for k, v in node.items():
                    reqs[_scalar_text(k)] = _scalar_text(v)
                    lines[_scalar_text(k)] = _line(k)
            else:
                for k, v in node.items():
                    if isinstance(v, (list, dict)):
                        take(v)

    for d in docs:
        take(d)
    return reqs, lines
