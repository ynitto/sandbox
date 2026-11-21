"""gitlab-issue-hub-agent — 決めた 1 つの GitLab の issue（ハブ）のための change-watch のプラグインと、コメントの本文を作るコマンド。

change-watch のプラグイン（register(api)）として、フィルタとコマンドを足す:

    change-watch --plugin gitlab_hub ...        （入れてあれば名前で。ほかの場所ならこのファイルのパス）
        フィルタ gitlab_issue_api      {{ env.GITLAB_HUB_ISSUE_URL | gitlab_issue_api }}
                                       issue の URL（https://gitlab.example.com/group/project/-/issues/42）を、その issue の
                                       API の URL（https://gitlab.example.com/api/v4/projects/group%2Fproject/issues/42）にする。
                                       設定の取得元と、ワークフローの call の URL に書く（ハブは issue の URL 1 つで決める）

    change-watch --plugin <このファイル> note --text "..."               本文をそのまま
    change-watch --plugin <このファイル> note --text-file F              ファイルの中身
    change-watch --plugin <このファイル> note -- <コマンド> [引数...]     コマンドの標準出力
        issue に足すコメントの本文を {"body": "..."} の JSON で出す（change-watch call の --json に渡す）。
        本文が空なら何も出さない。コマンドが失敗したら何も出さず、その終了コードで終わる。
        ファイル・コマンドの出力は UTF-8 で読み、読めなければ UTF-16（BOM つき）・この PC の文字コード（Shift_JIS など）で読む。
        文字化けした文字（U+FFFD）を含む本文は書かず、2 で終わる（渡される前に文字が失われている）。
        --out <ファイル> を付けると、JSON をそのファイルに書き、標準出力にはファイルのパスだけを出す（call の --json-file に渡す）。
        長い文は JSON にするとコマンド行に収まらないことがある（Windows は 8191 文字まで）ので、ファイルで渡す。
        --text が空で --text-file があれば、ファイルの中身を使う（ワークフローの引数を両方書いておける）。

    change-watch --plugin <このファイル> fields title="【完了】${CHANGE.record.title}" add_labels=完了
        issue の更新の JSON（{"title": "...", "add_labels": "..."}）を出す（change-watch call PUT の --json に渡す）。
        値はすべて文字列。Dagu の引数に、引用符を含む題などをそのまま渡せる。

    change-watch --plugin <このファイル> description --text "..." | --text-file F | -- <コマンド> [引数...]
        issue の説明を書き換える JSON（{"description": "..."}）を出す（change-watch call PUT の --json に渡す）。
        本文の渡し方と、空・失敗のときの扱いは note と同じ（ファイルやコマンドの出力から、何行もある説明を渡せる）。

    change-watch --plugin <このファイル> api-url [--url=<issue の URL>]
        issue の API の URL を出す（change-watch call の URL に渡す）。ハブでない issue を、ワークフローの引数で指して変えるときに使う。
        --url が無いか空なら、ハブの issue（環境変数 GITLAB_HUB_ISSUE_URL）。

    change-watch --plugin <このファイル> check-request [--kinds-file F] --text "..." | --text-file F | --change JSON | -- <コマンド> [引数...]
        依頼の書式を調べて、結果を JSON で出す。どの種別で何を動かすかはワークフローの preconditions が決める。

    change-watch --plugin <このファイル> check-old-note --change JSON --author <ユーザー名> [--days 7]
        get が出したコメントの変化を見て、消してよいかを 1 語で出す（古いコメントを消すワークフローの preconditions で分ける）:
        delete（--author が書いた、--days 日より古いコメント）・wait（--author が書いたが、まだ新しい）・keep（それ以外。消さない）。

    これらのコマンドは設定を読まない（--config・--connections は要らない）。python3 <このファイル> note ... と直接呼んでも同じ。

Dagu の引数に任意の文（改行・引用符）を JSON として渡せるようにし、書き込む文に混ざった GitLab のクイックアクション
（行頭の /close など）を効かなくする。check-request は届いた依頼の書式を調べる。
どの変化をどう扱うかの条件は持たない（Dagu のワークフローの preconditions に書く）。HTTP は送らない（取得・書き込み・認証・
要求の間隔は change-watch が受け持つ）。標準ライブラリだけで動く。設計は docs/designs/gitlab-issue-hub-agent-design.md。
"""
from __future__ import annotations

import argparse
import datetime
import json
import locale
import os
import re
import subprocess
import sys
import urllib.parse

OK = 0
USAGE = 2
NOT_FOUND = 127

_QUICK_ACTION = re.compile(r"(?m)^(\s*)/")
_ISSUE_URL = re.compile(r"^(https?://[^/?#]+)/(.+?)(?:/-)?/(?:issues|work_items)/(\d+)/?(?:[?#].*)?$")

_KIND_NAME = r"[a-z][a-z0-9-]{0,31}"
_REQUEST_HEAD = re.compile(rf"^【依頼】[ \u3000]*\[(?P<kind>{_KIND_NAME})\][ \u3000]*(?P<subject>\S.*)$")
_FIELD_LINE = re.compile(r"^[ \t]*(?:[-*][ \t]+)?(?P<name>[^:：\s][^:：]{0,39}?)[ \t\u3000]*[:：][ \t\u3000]*(?P<value>.*)$")
_DATE = r"^\d{4}-\d{2}-\d{2}$"
COMMON_FIELDS = {"差出人": {}, "受信日時": {}, "受付番号": {}}
REQUEST_KINDS = {
    "report": {"title": "集計・報告の作成", "text_required": False, "fields": {
        "対象": {"required": True}, "期間": {"required": True},
        "期限": {"pattern": _DATE, "hint": "2026-11-30 のように書いてください"}, "提出先": {}}},
    "inquiry": {"title": "問い合わせへの回答案", "text_required": True, "fields": {
        "差出人": {"required": True}, "受信日時": {"required": True},
        "期限": {"pattern": _DATE, "hint": "2026-11-30 のように書いてください"}}},
    "account": {"title": "アカウント・権限の申請", "text_required": False, "fields": {
        "対象者": {"required": True},
        "操作": {"required": True, "pattern": r"^(追加|変更|削除)$", "hint": "追加・変更・削除 のどれかを書いてください"},
        "期日": {"required": True, "pattern": _DATE, "hint": "2026-12-01 のように書いてください"}, "権限": {}}},
}


# ---------------------------------------------------------------------------
# プラグイン（change-watch --plugin で読み込む）
# ---------------------------------------------------------------------------

def gitlab_issue_api(url, _arg=None) -> str:
    """issue の URL を、その issue の API の URL にする（プロジェクトのパスは %2F で 1 つにまとめる）。
    GitLab を https://host/gitlab/ のような下の場所に置いているときは見分けられないので、API の URL をそのまま書く
    （/api/v4/ を含む URL は手を加えずに返す）。"""
    url = str(url or "").strip()
    if "/api/v4/" in url:
        return url.rstrip("/")
    m = _ISSUE_URL.match(url)
    if not m:
        raise ValueError(f"GitLab の issue の URL ではありません: {url!r}"
                         "（https://gitlab.example.com/group/project/-/issues/42 の形で書いてください）")
    base, project, iid = m.groups()
    return f"{base}/api/v4/projects/{urllib.parse.quote(project, safe='')}/issues/{iid}"


# ---------------------------------------------------------------------------
# コメントの本文・説明・更新の項目（Dagu のワークフローから、change-watch のコマンドとして呼ぶ）
# ---------------------------------------------------------------------------

def disarm(text: str) -> str:
    """行頭の / を \\/ にして、GitLab のクイックアクション（/close・/assign など）として効かないようにする。
    Markdown では \\/ は / と表示される。"""
    return _QUICK_ACTION.sub(lambda m: m.group(1) + "\\/", text)


def note_json(text: str, field: str = "body") -> str:
    return json.dumps({field: disarm(text.strip())})         # ASCII だけで出す（Windows のコンソールの文字コードに依らない）


_BROKEN = "\ufffd"                        # 読めなかった文字の置き換え（ここまで来たら、元の文字はもう無い）


def _encodings() -> list[str]:
    """本文のバイト列を読む文字コードの順。UTF-8 を先に、読めなければこの PC の文字コード（日本語の Windows なら cp932）。
    UTF-8 モードの Python でも、この PC の本来の文字コードを使う（locale.getencoding）。"""
    native = getattr(locale, "getencoding", lambda: locale.getpreferredencoding(False))()
    out: list[str] = []
    for enc in ("utf-8", native, "cp932"):
        if enc and enc.lower().replace("_", "-") not in [e.lower().replace("_", "-") for e in out]:
            out.append(enc)
    return out


def decode(raw: bytes) -> tuple[str | None, str]:
    """バイト列を文字にする。(文字, 使った文字コード)。BOM があればそれに従い、無ければ UTF-8・この PC の文字コード・cp932 の順に
    試す。どれでも読めなければ (None, "")（読めない文字を黙って置き換えない）。"""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if raw.startswith(bom):
            try:
                return raw.decode(enc), enc
            except UnicodeError:
                return None, ""
    for enc in _encodings():
        try:
            return raw.decode(enc), enc
        except (UnicodeError, LookupError):
            continue
    return None, ""


def _run(command: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(command, capture_output=True,
                           env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
    except OSError as e:
        print(f"コマンドを動かせません: {e}", file=sys.stderr)
        return NOT_FOUND, ""
    if p.stderr:
        err, _ = decode(p.stderr)
        sys.stderr.write(err if err is not None else p.stderr.decode("utf-8", "replace"))   # Dagu のログに残す（コメントには書かない）
    if p.returncode != 0:
        print(f"コマンドが終了コード {p.returncode} で終わりました", file=sys.stderr)
        return p.returncode, ""
    text, _ = decode(p.stdout)
    if text is None:
        print("コマンドの出力を文字として読めません（UTF-8・Shift_JIS のどちらでもありません）", file=sys.stderr)
        return USAGE, ""
    return OK, text


def _violation(code: str, field: str, line: int, message: str) -> dict:
    return {"code": code, "field": field, "line": line, "message": message}


def parse_request(text: str) -> dict:
    """本文の形を読み、項目・行番号を取り出す（レジストリは見ない）。"""
    text = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    first, *lines = text.split("\n")
    first = first.rstrip()
    is_request = first.startswith("【依頼】")
    result = {"is_request": is_request, "kind": "", "subject": "", "head_violation": None,
              "items": [], "bad_lines": [], "text_line": 0}
    if not is_request:
        return result
    match = _REQUEST_HEAD.fullmatch(first)
    if match:
        result.update(kind=match["kind"], subject=match["subject"].rstrip())
    else:
        rest = first.removeprefix("【依頼】").lstrip(" \u3000")
        tag = re.fullmatch(r"\[(" + _KIND_NAME + r")\]", rest)
        if tag:
            result["kind"] = tag[1]
            error = ("no_subject", "1 行目に件名がありません。種別のあとに件名を書いてください")
        elif rest.startswith(("[", "［")):
            token = re.match(r"(?:\[[^\]]*\]|［[^］]*］|\S+)", rest)[0]
            error = ("bad_kind", f"1 行目の種別「{token}」の書き方が違います。"
                     "半角の小文字・数字・ハイフンを、半角の [ ] で囲んでください")
        else:
            error = ("no_kind", "1 行目に種別がありません。「【依頼】[種別] 件名」の形で書いてください")
        result["head_violation"] = _violation(error[0], "1 行目", 1, error[1])
        return result
    for line, content in enumerate(lines, 2):
        if not content.strip():
            result["text_line"] = line + 1
            break
        field = _FIELD_LINE.fullmatch(content)
        if field:
            result["items"].append((line, field["name"].strip(), field["value"].strip()))
        else:
            result["bad_lines"].append((line, content))
    return result


def check_request(text: str, kinds: dict | None = None) -> dict:
    """規約に照らして、振り分け先と違反の一覧を返す。"""
    kinds = REQUEST_KINDS if kinds is None else kinds
    parsed = parse_request(text)
    kind = parsed["kind"]
    fields, unknown, violations = {}, [], []
    result = {"ok": False, "route": "none", "kind": kind, "subject": parsed["subject"],
              "fields": fields, "unknown": unknown, "text_line": 0, "violations": violations}
    if not parsed["is_request"]:
        return result
    result["route"] = "invalid"
    available = f"（使える種別: {'・'.join(kinds)}）"
    head_error = parsed["head_violation"]
    if head_error:
        if head_error["code"] == "no_kind":
            head_error["message"] += available
        violations.append(head_error)
        return result
    if kind not in kinds:
        violations.append(_violation("unknown_kind", "1 行目", 1, f"種別 [{kind}] は決まっていません{available}"))
        return result

    definition = kinds[kind]
    rules = {**COMMON_FIELDS, **definition["fields"]}
    seen = {}
    for line, name, value in parsed["items"]:
        if name in seen:
            first_line, _ = seen[name]
            violations.append(_violation("duplicate", name, line,
                                         f"項目「{name}」が 2 回あります（{first_line} 行目と {line} 行目）。1 回だけ書いてください"))
            continue
        seen[name] = (line, value)
        if value:
            fields[name] = value
            if name not in rules:
                unknown.append(name)
    for line, content in parsed["bad_lines"]:
        quote = content[:30] + ("…" if len(content) > 30 else "")
        violations.append(_violation("bad_line", f"{line} 行目", line,
                                     f"{line} 行目「{quote}」は「項目名: 値」の形ではありません。"
                                     "自由な文は、空行を 1 つ入れたあとに書いてください"))
    required = "・".join(name for name, rule in rules.items() if rule.get("required"))
    for name, rule in rules.items():
        line, value = seen.get(name, (0, ""))
        if rule.get("required") and not line:
            violations.append(_violation("missing", name, 0, f"項目「{name}」がありません（{kind} には {required} が要ります）"))
        elif rule.get("required") and not value:
            violations.append(_violation("empty", name, line, f"項目「{name}」の値が空です"))
        elif value and "pattern" in rule and not re.search(rule["pattern"], value):
            hint = rule.get("hint", "")
            message = f"項目「{name}」の値「{value}」が違います" + (f"。{hint}" if hint else "")
            violations.append(_violation("bad_value", name, line, message))
    result["text_line"] = parsed["text_line"]
    if definition["text_required"] and not result["text_line"]:
        violations.append(_violation("no_text", "本文", 0,
                                     "自由な文がありません。項目のあとに空行を 1 つ入れて書いてください"))
    violations.sort(key=lambda v: (v["line"] == 0, v["line"]))
    if not violations:
        result.update(ok=True, route=kind)
    return result


def load_kinds(path: str) -> dict:
    """種別の JSON の形を確かめ、組み込みの種別に重ねる。"""
    try:
        with open(path, encoding="utf-8") as f:
            extra = json.load(f)
    except (OSError, ValueError, RecursionError) as e:
        raise ValueError(f"種別のファイルを読めません: {e}") from e
    if not isinstance(extra, dict):
        raise ValueError("種別のファイルは JSON のオブジェクトで書いてください")
    for kind, spec in extra.items():
        if not re.fullmatch(_KIND_NAME, kind) or kind in {"invalid", "none"}:
            raise ValueError(f"種別名「{kind}」は使えません")
        if (not isinstance(spec, dict) or set(spec) != {"title", "text_required", "fields"}
                or not isinstance(spec["title"], str) or not isinstance(spec["text_required"], bool)
                or not isinstance(spec["fields"], dict)):
            raise ValueError(f"種別 [{kind}] は title・text_required・fields を正しい形で書いてください")
        for name, rule in spec["fields"].items():
            label = f"種別 [{kind}] の項目「{name}」"
            if (name != name.strip() or not re.fullmatch(r"[^:：\s][^:：\r\n]{0,39}", name)
                    or not isinstance(rule, dict) or rule.keys() - {"required", "pattern", "hint"}
                    or not isinstance(rule.get("required", False), bool)
                    or not isinstance(rule.get("pattern", ""), str)
                    or not isinstance(rule.get("hint", ""), str)):
                raise ValueError(f"{label}の名前か定義の形が違います")
            if "pattern" in rule:
                try:
                    re.compile(rule["pattern"])
                except (re.error, OverflowError, RecursionError) as e:
                    raise ValueError(f"{label}の pattern が正規表現ではありません: {e}") from e
    return {**REQUEST_KINDS, **extra}


def _read_text(a) -> tuple[int, str]:
    command = list(a.command or [])
    if command[:1] == ["--"]:
        command = command[1:]
    change = getattr(a, "change", None)
    others = (bool(a.text_file), bool(command), change is not None)
    given = (a.text is not None and (a.text != "" or not any(others)), *others)   # 空の --text は、ほかの渡し方があれば無いものとする
    if sum(given) != 1:
        extra = "・--change" if a.cmd == "check-request" else ""
        print(f"本文は --text・--text-file・-- <コマンド>{extra} のどれか 1 つで渡してください", file=sys.stderr)
        return USAGE, ""
    if command:
        return _run(command)
    if change is not None:
        try:
            text = json.loads(change)["record"]["body"]
            if not isinstance(text, str):
                raise TypeError("record.body")
        except (ValueError, KeyError, TypeError, RecursionError):
            print("--change は record.body に本文を持つ JSON で渡してください", file=sys.stderr)
            return USAGE, ""
        return OK, text
    if a.text_file:
        try:
            with open(a.text_file, "rb") as f:
                text, _ = decode(f.read())
        except OSError as e:
            print(f"本文のファイルを読めません: {e}", file=sys.stderr)
            return USAGE, ""
        if text is None:
            print("本文のファイルを文字として読めません（UTF-8・UTF-16・Shift_JIS のどれでもありません）", file=sys.stderr)
            return USAGE, ""
        return OK, text.replace("\r\n", "\n")
    return OK, a.text


def cmd_note(a) -> int:
    code, text = _read_text(a)
    if code != OK:
        return code
    if not text.strip():
        return OK                           # 空なら何も出さない（ワークフローは書き込みのステップを飛ばす）
    if _refuse_broken(text):
        return USAGE
    return _emit(note_json(text, "description" if a.cmd == "description" else "body"), a.out)


def _refuse_broken(text: str) -> bool:
    """文字化け（U+FFFD）を含む文は書かない。渡される前に、UTF-8 でない文字が置き換えられている。"""
    if _BROKEN not in text:
        return False
    line = text[:text.index(_BROKEN)].count("\n") + 1
    print(f"本文の {line} 行目に文字化けした文字（U+FFFD）があるので書きません。本文が渡される前に、UTF-8 でない文字が"
          "置き換えられています。前のステップの出力を ${…} で渡しているなら、ファイル（--text-file）か -- <コマンド> で"
          "渡してください（Shift_JIS のファイル・出力も読めます）", file=sys.stderr)
    return True


def _emit(payload: str, out: str | None) -> int:
    """JSON を標準出力に出す。--out があればファイルに書いて、パスだけを出す（長い JSON をコマンド行に載せないため）。"""
    if not out:
        print(payload)
        return OK
    try:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(payload)
    except OSError as e:
        print(f"--out のファイルに書けません: {e}", file=sys.stderr)
        return USAGE
    print(out)
    return OK


def cmd_check_request(a) -> int:
    code, text = _read_text(a)
    if code != OK:
        return code
    try:
        kinds = load_kinds(a.kinds_file) if a.kinds_file is not None else None
    except ValueError as e:
        print(e, file=sys.stderr)
        return USAGE
    print(json.dumps(check_request(text, kinds)))
    return OK


def old_note(change: dict, author: str, days: int, now: datetime.datetime | None = None) -> str:
    """コメントの変化を、消す（delete）・まだ待つ（wait）・消さない（keep）に分ける。
    消すのは author が書いた、人のコメント（system でない）で、days 日より古いものだけ。日時を読めなければ消さない。"""
    record = change.get("record")
    if change.get("kind") not in ("appeared", "changed") or not isinstance(record, dict) or record.get("system") is not False:
        return "keep"
    writer = record.get("author")
    if not isinstance(writer, dict) or writer.get("username") != author:
        return "keep"
    try:
        created = datetime.datetime.fromisoformat(str(record.get("created_at")).replace("Z", "+00:00"))
    except ValueError:
        return "keep"
    if created.tzinfo is None:
        created = created.replace(tzinfo=datetime.timezone.utc)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return "delete" if now - created > datetime.timedelta(days=days) else "wait"


def cmd_check_old_note(a) -> int:
    author = (a.author or "").strip()
    if not author:
        print("--author に、消してよいコメントの書き手（トークンの持ち主のユーザー名）を渡してください", file=sys.stderr)
        return USAGE
    try:
        days = int(a.days)
        if days < 1:
            raise ValueError
    except ValueError:
        print("--days は 1 以上の整数で渡してください（付けてすぐ消すと、メールが送られないことがあります）", file=sys.stderr)
        return USAGE
    if not (a.change or "").strip():
        return OK                           # 受け取った変化が無ければ何も出さない
    try:
        change = json.loads(a.change)
        if not isinstance(change, dict):
            raise TypeError
    except (ValueError, TypeError, RecursionError):
        print("--change は get が出した JSON で渡してください", file=sys.stderr)
        return USAGE
    print(old_note(change, author, days))
    return OK


def cmd_api_url(a) -> int:
    url = (a.url or "").strip() or os.environ.get("GITLAB_HUB_ISSUE_URL", "")
    try:
        api_url = gitlab_issue_api(url)
        parts = urllib.parse.urlsplit(api_url)
        if (parts.scheme not in ("http", "https") or not parts.hostname
                or not re.fullmatch(r".*/api/v4/projects/[^/]+/issues/[0-9]+", parts.path)):
            raise ValueError("GitLab の issue の API URL ではありません")
    except ValueError as e:
        print(e, file=sys.stderr)
        return USAGE
    print(api_url)
    return OK


def cmd_fields(a) -> int:
    out: dict[str, str] = {}
    for item in a.pairs:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            print(f"項目は 名前=値 で書いてください: {item!r}", file=sys.stderr)
            return USAGE
        out[key.strip()] = disarm(value) if key.strip() == "description" else value
    if any(_refuse_broken(v) for v in out.values()):
        return USAGE
    if not out:
        print("項目がありません（title=... のように渡してください）", file=sys.stderr)
        return USAGE
    return _emit(json.dumps(out), a.out)


_CHECK = "依頼の書式を調べて、結果を JSON で出す（ワークフローの preconditions で種別ごとに分ける）"
_NOTE = "コメントの本文を {\"body\": ...} で出す（change-watch call の --json に渡す）"
_DESCRIPTION = "issue の説明を {\"description\": ...} で出す（change-watch call PUT の --json に渡す）"
_FIELDS = "issue の更新の JSON を 名前=値 から出す（change-watch call PUT の --json に渡す）"
_OLD_NOTE = "コメントの変化を見て、消してよいか（delete・wait・keep）を出す（古いコメントを消すワークフローの preconditions で分ける）"
_OUT = "JSON をこのファイルに書き、標準出力にはパスだけを出す（call の --json-file に渡す。長い文をコマンド行に載せない）"
_API_URL = "issue の API の URL を出す（change-watch call の URL に渡す）"


def _text_args(p, cmd: str) -> None:
    p.set_defaults(cmd=cmd)
    p.add_argument("--text", default=None, help="本文（- で始まる文も渡せるよう、--text=... の形で書く）")
    p.add_argument("--text-file", help="本文のファイル（UTF-8）")
    p.add_argument("--out", help=_OUT)
    p.add_argument("command", nargs=argparse.REMAINDER, help="-- のあとに書いたコマンドの標準出力を本文にする")


def _check_args(p) -> None:
    _text_args(p, "check-request")              # --out は使わない（結果は短い）
    p.add_argument("--change", help="get が出した JSON（record.body を調べる）")
    p.add_argument("--kinds-file", help="追加・置き換えする種別の JSON ファイル（UTF-8）")


def _fields_args(p) -> None:
    p.add_argument("--out", help=_OUT)
    p.add_argument("pairs", nargs="+", metavar="名前=値", help="title=...・add_labels=...・description=...・state_event=close など")


def _old_note_args(p) -> None:
    p.add_argument("--change", default="", help="get が出した JSON（空なら何も出さない）")
    p.add_argument("--author", default="", help="消してよいコメントの書き手（トークンの持ち主のユーザー名）")
    p.add_argument("--days", default="7", help="この日数より古いコメントを消す（既定 7。1 以上）")


def _api_url_args(p) -> None:
    p.add_argument("--url", default="", help="issue の URL（無いか空なら、環境変数 GITLAB_HUB_ISSUE_URL のハブの issue）")


def register(api) -> None:
    """フィルタと、ワークフローが呼ぶコマンド（change-watch --plugin <このファイル> note ... など。設定は読まない）。"""
    api.add_filter("gitlab_issue_api", gitlab_issue_api)
    _text_args(api.add_command("note", lambda _cfg, _store, a: cmd_note(a), _NOTE, needs_config=False), "note")
    _text_args(api.add_command("description", lambda _cfg, _store, a: cmd_note(a), _DESCRIPTION, needs_config=False), "description")
    _check_args(api.add_command("check-request", lambda _cfg, _store, a: cmd_check_request(a), _CHECK, needs_config=False))
    _fields_args(api.add_command("fields", lambda _cfg, _store, a: cmd_fields(a), _FIELDS, needs_config=False))
    _api_url_args(api.add_command("api-url", lambda _cfg, _store, a: cmd_api_url(a), _API_URL, needs_config=False))
    _old_note_args(api.add_command("check-old-note", lambda _cfg, _store, a: cmd_check_old_note(a), _OLD_NOTE, needs_config=False))


def main(argv: list[str] | None = None) -> int:
    """python3 <このファイル> ... で直接呼ぶ口（change-watch を通すのと同じコマンド）。"""
    p = argparse.ArgumentParser(prog="gitlab_hub.py", description="issue に書くもの（コメントの本文・説明・更新の項目）を JSON で出す")
    sub = p.add_subparsers(dest="cmd", required=True)
    _text_args(sub.add_parser("note", help=_NOTE), "note")
    _text_args(sub.add_parser("description", help=_DESCRIPTION), "description")
    _check_args(sub.add_parser("check-request", help=_CHECK))
    _fields_args(sub.add_parser("fields", help=_FIELDS))
    _api_url_args(sub.add_parser("api-url", help=_API_URL))
    _old_note_args(sub.add_parser("check-old-note", help=_OLD_NOTE))
    a = p.parse_args(argv)
    return {"note": cmd_note, "description": cmd_note, "api-url": cmd_api_url, "fields": cmd_fields,
            "check-request": cmd_check_request, "check-old-note": cmd_check_old_note}[a.cmd](a)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):      # 案内の文が、コンソールの文字コードで書けなくても止まらない
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    sys.exit(main())
