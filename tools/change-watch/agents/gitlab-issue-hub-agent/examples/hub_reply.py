"""依頼への返信と差し戻しを作る共通の部品。"""
from __future__ import annotations

import json


def load(argv: list[str]) -> tuple[dict, dict]:
    """検証の結果とコメントの変化を読む。"""
    return json.loads(argv[1]), json.loads(argv[2])


def _body(change: dict) -> str:
    # gitlab_hub.parse_request と同じ行番号で本文を読む。
    return change["record"]["body"].removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()


def head(prefix: str, check: dict, change: dict) -> str:
    """返信の頭に、元のコメントと受付番号を写す。"""
    record = change["record"]
    kind = check["kind"]
    tag = f"[{kind}]" if kind else ""
    subject = check["subject"]
    if not subject:
        subject = _body(change).partition("\n")[0].removeprefix("【依頼】").strip()
        if tag:
            subject = subject.removeprefix(tag).lstrip()
    title = (f"{tag} {subject}" if tag else subject).rstrip()
    lines = [f"{prefix}{title}", f"元の依頼: note_{record['id']}（{record['author']['username']} さん）"]
    if "受付番号" in check["fields"]:
        lines.append(f"受付番号: {check['fields']['受付番号']}")
    return "\n".join(lines)


def free_text(check: dict, change: dict) -> str:
    """自由な文が始まる行から末尾までを取り出す。"""
    line = check["text_line"]
    return "\n".join(_body(change).split("\n")[line - 1:]) if line else ""
