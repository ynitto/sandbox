#!/usr/bin/env python3
"""Copilot の夜間定型メニューから依頼文を作る。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import copilot_app_job as job

MENU_DIR = Path(__file__).resolve().parent / "menu"
MENUS = {
    "docs-followup": "直近のコード変更に合わせたドキュメント更新の下書き",
    "test-maintenance": "テストケース・テストコード追加や更新の下書き",
    "changelog-draft": "[Unreleased] への追記案",
    "drift-scan": "乖離点検の結果一覧と対応優先度の下書き",
}
PLACEHOLDER = re.compile(r"\{\{([a-z][a-z0-9_]*)(?:\|([^{}\n]*))?\}\}")


def render(menu: str, raw_params: list[str]) -> str:
    if menu not in MENUS:
        raise ValueError(f"未知のメニュー「{menu}」。利用可能なメニューは list で確認できます。")
    template = (MENU_DIR / f"{menu}.md").read_text(encoding="utf-8")
    defaults = {match[1]: match[2] for match in PLACEHOLDER.finditer(template)}
    params = {}
    for raw in raw_params:
        name, separator, value = raw.partition("=")
        if not name or not separator:
            raise ValueError(f"--param は k=v 形式で指定してください（{raw}）。")
        if name not in defaults:
            raise ValueError(f"未知のパラメータ「{name}」（メニュー {menu}）。")
        if name in params:
            raise ValueError(f"パラメータ「{name}」が重複しています。")
        params[name] = value

    missing = [name for name, default in defaults.items() if default is None and not params.get(name, "").strip()]
    if missing:
        raise ValueError(f"必須パラメータが不足しています（{menu}）: {', '.join(missing)}")

    return PLACEHOLDER.sub(lambda match: params.get(match[1], defaults[match[1]]), template).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Copilot の夜間定型メニューから依頼文を作る")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="メニューの一覧を出す")
    generate = sub.add_parser("generate", help="依頼文を展開する")
    submit = sub.add_parser("submit", help="依頼を受け箱に置いて ID を出す")
    for command in (generate, submit):
        command.add_argument("--menu", required=True, help="メニュー名")
        command.add_argument("--param", action="append", default=[], metavar="k=v", help="入力パラメータ（複数指定可）")
    generate.add_argument("--out", type=Path, help="依頼文を保存するファイル（既定は標準出力）")
    submit.add_argument("--dir", required=True, help="アプリで開いている作業フォルダ")
    submit.add_argument("--name", default="", help="依頼の見分け用の名前")
    submit.add_argument("--box", help=f"受け箱の場所（既定は 作業フォルダ/{job.BOX_NAME}）")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.command == "list":
        for name, purpose in MENUS.items():
            print(f"{name}\t{purpose}")
        return job.EXIT_OK

    try:
        prompt = render(args.menu, args.param)
    except (ValueError, OSError) as exc:
        print(exc, file=sys.stderr)
        return job.EXIT_USAGE

    if args.command == "submit":
        work_dir = job.work_dir_of(args)
        if work_dir is None:
            return job.EXIT_USAGE
        print(job.submit(job.box_of(work_dir, args.box), prompt, args.name))
    elif args.out is None:
        sys.stdout.write(prompt)
    else:
        try:
            args.out.write_text(prompt, encoding="utf-8")
        except OSError as exc:
            print(f"依頼文を保存できません（{args.out}）: {exc}", file=sys.stderr)
            return job.EXIT_USAGE
    return job.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
