#!/usr/bin/env python3
"""
save_memory.py - 記憶ファイルを作成・更新するスクリプト

Usage:
  # 新規作成（インタラクティブ）
  python save_memory.py

  # 引数指定
  python save_memory.py --category auth --title "JWTの有効期限設定" \
    --summary "JWTを15分に設定した理由" --tags jwt,auth --content "詳細内容"

  # 既存ファイルを更新
  python save_memory.py --update memories/auth/jwt-expiry.md
"""

import argparse
import datetime
import os
import re
import sys


MEMORIES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "memories")

TEMPLATE = """\
---
id: {id}
title: "{title}"
created: "{date}"
updated: "{date}"
status: active
tags: [{tags}]
related: []
summary: "{summary}"
---

# {title}

## コンテキスト
{context}

## 詳細
{content}

## 学び・結論
{conclusion}
"""


def slugify(text: str) -> str:
    # ASCII英数字とハイフンのみ許可（日本語等はハイフンに変換）
    text = text.lower()
    text = re.sub(r"[^\x00-\x7f]", "-", text)   # 非ASCII → ハイフン
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:50] or "memory"


def generate_id(date_str: str, category_dir: str) -> str:
    base = f"mem-{date_str.replace('-', '')}"
    existing = []
    if os.path.isdir(category_dir):
        for f in os.listdir(category_dir):
            if f.endswith(".md"):
                existing.append(f)
    n = len(existing) + 1
    return f"{base}-{n:03d}"


def save_memory(category: str, title: str, summary: str, content: str,
                tags: list, context: str = "", conclusion: str = "") -> str:
    date_str = datetime.date.today().isoformat()
    slug = slugify(title)
    category_dir = os.path.join(MEMORIES_DIR, category)
    os.makedirs(category_dir, exist_ok=True)

    mem_id = generate_id(date_str, category_dir)
    filepath = os.path.join(category_dir, f"{slug}.md")

    # 同名ファイルが存在する場合はサフィックスを追加
    if os.path.exists(filepath):
        base, ext = os.path.splitext(filepath)
        filepath = f"{base}-{mem_id[-3:]}{ext}"

    tags_str = ", ".join(tags)
    body = TEMPLATE.format(
        id=mem_id,
        title=title,
        date=date_str,
        tags=tags_str,
        summary=summary,
        context=context or "(作成時に記録なし)",
        content=content,
        conclusion=conclusion or "(作成時に記録なし)",
    )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(body)

    return filepath


def update_memory(filepath: str, summary: str = None, content: str = None,
                  conclusion: str = None, status: str = None) -> None:
    """既存記憶ファイルのupdated日付とフィールドを更新する"""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    date_str = datetime.date.today().isoformat()
    text = re.sub(r'^updated: ".*"', f'updated: "{date_str}"', text, flags=re.MULTILINE)

    if status:
        text = re.sub(r"^status: \w+", f"status: {status}", text, flags=re.MULTILINE)
    if summary:
        text = re.sub(r'^summary: ".*"', f'summary: "{summary}"', text, flags=re.MULTILINE)
    if content:
        text = re.sub(r"(## 詳細\n).*?(## 学び・結論)",
                      f"\\1{content}\n\n\\2", text, flags=re.DOTALL)
    if conclusion:
        text = re.sub(r"(## 学び・結論\n).*?(\Z|## )",
                      f"\\1{conclusion}\n\n\\2", text, flags=re.DOTALL)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    parser = argparse.ArgumentParser(description="記憶ファイルを保存・更新する")
    parser.add_argument("--category", default="general", help="カテゴリ名 (default: general)")
    parser.add_argument("--title", help="記憶タイトル")
    parser.add_argument("--summary", help="要約（1〜2文）")
    parser.add_argument("--content", help="詳細内容")
    parser.add_argument("--tags", default="", help="カンマ区切りタグ")
    parser.add_argument("--context", default="", help="背景・コンテキスト")
    parser.add_argument("--conclusion", default="", help="学び・結論")
    parser.add_argument("--update", help="更新対象ファイルパス")
    parser.add_argument("--status", choices=["active", "archived", "deprecated"],
                        help="ステータス変更（--updateと組み合わせて使用）")
    args = parser.parse_args()

    if args.update:
        update_memory(
            args.update,
            summary=args.summary,
            content=args.content,
            conclusion=args.conclusion,
            status=args.status,
        )
        print(f"Updated: {args.update}")
        return

    # 必須項目が未指定の場合はインタラクティブ入力
    title = args.title or input("タイトル: ").strip()
    summary = args.summary or input("要約（1〜2文）: ").strip()
    content = args.content or input("詳細内容: ").strip()
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    if not tags:
        raw = input("タグ（カンマ区切り、空欄でOK）: ").strip()
        tags = [t.strip() for t in raw.split(",") if t.strip()]

    filepath = save_memory(
        category=args.category,
        title=title,
        summary=summary,
        content=content,
        tags=tags,
        context=args.context,
        conclusion=args.conclusion,
    )
    print(f"Saved: {filepath}")


if __name__ == "__main__":
    main()
