#!/usr/bin/env python3
"""
save_memory.py - 記憶ファイルを作成・更新するスクリプト

Usage:
  # 新規作成（インタラクティブ）
  python save_memory.py

  # 引数指定（ワークスペース記憶）
  python save_memory.py --category auth --title "JWTの有効期限設定" \
    --summary "JWTを15分に設定した理由" --tags jwt,auth --content "詳細内容"

  # ホーム記憶として保存（複数プロジェクト横断）
  python save_memory.py --scope home --category architecture --title "..."

  # 既存ファイルを更新
  python save_memory.py --update memories/auth/jwt-expiry.md --summary "新しい要約"
"""

import argparse
import os
import re
import sys

import auto_tagger
import memory_utils
import similarity


TEMPLATE = """\
---
id: {id}
title: "{title}"
created: "{date}"
updated: "{date}"
status: active
scope: {scope}
tags: [{tags}]
related: []
access_count: 0
last_accessed: ""
user_rating: 0
correction_count: 0
share_score: 0
promoted_from: ""
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
    text = text.lower()
    text = text.replace(" ", "-").replace("_", "-")
    # 非ASCII をハイフンへ変換
    text = "".join(c if c.isascii() and (c.isalnum() or c == "-") else "-" for c in text)
    # 連続ハイフンをまとめる
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:50] or "memory"


def generate_id(date_str: str, category_dir: str) -> str:
    base = f"mem-{date_str.replace('-', '')}"
    n = 1
    if os.path.isdir(category_dir):
        n = sum(1 for f in os.listdir(category_dir) if f.endswith(".md")) + 1
    return f"{base}-{n:03d}"


def save_memory(category: str, title: str, summary: str, content: str,
                tags: list, scope: str = "workspace",
                context: str = "", conclusion: str = "") -> str:
    date_str = memory_utils.today_str()
    slug = slugify(title)
    memory_dir = memory_utils.get_memory_dir(scope)
    category_dir = os.path.join(memory_dir, category)
    os.makedirs(category_dir, exist_ok=True)

    mem_id = generate_id(date_str, category_dir)
    filepath = os.path.join(category_dir, f"{slug}.md")
    if os.path.exists(filepath):
        base, ext = os.path.splitext(filepath)
        n = 1
        while os.path.exists(f"{base}-{n:03d}{ext}"):
            n += 1
        filepath = f"{base}-{n:03d}{ext}"

    # share_score を事前計算（保存直後は access_count=0 のため情報量とタグのみ）
    pseudo_meta = {"tags": tags, "access_count": 0, "user_rating": 0,
                   "correction_count": 0, "status": "active"}
    share_score = memory_utils.compute_share_score(pseudo_meta, content)

    tags_str = ", ".join(tags)
    body = TEMPLATE.format(
        id=mem_id,
        title=title,
        date=date_str,
        scope=scope,
        tags=tags_str,
        summary=summary,
        context=context or "(作成時に記録なし)",
        content=content,
        conclusion=conclusion or "(作成時に記録なし)",
    )
    # share_score を埋め込む
    body = re.sub(r"^share_score: \d+", f"share_score: {share_score}", body, count=1, flags=re.MULTILINE)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(body)

    # インデックスを更新
    memory_dir = memory_utils.get_memory_dir(scope)
    memory_utils.update_index_entry(memory_dir, filepath)

    return filepath


def update_memory(filepath: str, summary: str = None, content: str = None,
                  conclusion: str = None, status: str = None) -> None:
    """既存記憶ファイルの updated 日付と各フィールドを更新する"""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    date_str = memory_utils.today_str()
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

    # share_score を再計算
    meta, body_text = memory_utils.parse_frontmatter(text)
    new_score = memory_utils.compute_share_score(meta, body_text)
    text = re.sub(r"^share_score: -?\d+", f"share_score: {new_score}", text, flags=re.MULTILINE)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(text)

    # インデックスを更新
    memory_dir = memory_utils.find_memory_dir(filepath)
    if memory_dir:
        memory_utils.update_index_entry(memory_dir, filepath)


def main():
    parser = argparse.ArgumentParser(description="記憶ファイルを保存・更新する")
    parser.add_argument("--category", default="general", help="カテゴリ名 (default: general)")
    parser.add_argument("--scope", default="workspace",
                        choices=["workspace", "home"],
                        help="スコープ: workspace(プロジェクト固有) | home(横断) (default: workspace)")
    parser.add_argument("--title", help="記憶タイトル")
    parser.add_argument("--summary", help="要約（1〜2文）")
    parser.add_argument("--content", help="詳細内容")
    parser.add_argument("--tags", default="", help="カンマ区切りタグ")
    parser.add_argument("--context", default="", help="背景・コンテキスト")
    parser.add_argument("--conclusion", default="", help="学び・結論")
    parser.add_argument("--update", help="更新対象ファイルパス")
    parser.add_argument("--status", choices=["active", "archived", "deprecated"],
                        help="ステータス変更（--update と組み合わせて使用）")
    # v4 新機能
    parser.add_argument("--no-dedup", action="store_true",
                        help="類似チェックをスキップ（自動保存・スクリプト呼び出し時）")
    parser.add_argument("--dedup-report", action="store_true",
                        help="類似記憶があっても保存し、類似一覧を stdout に出力（非インタラクティブモード）")
    parser.add_argument("--dedup-threshold", type=float, default=0.65,
                        help="類似度閾値（この値以上で警告、default: 0.65）")
    parser.add_argument("--no-auto-tags", action="store_true",
                        help="自動タグ付与を無効化")
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

    # 入力収集
    title = args.title or input("タイトル: ").strip()
    summary = args.summary or input("要約（1〜2文）: ").strip()
    content = args.content or input("詳細内容: ").strip()
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    if not tags and not args.no_auto_tags:
        raw = input("タグ（カンマ区切り、空欄でOK / 自動補完あり）: ").strip()
        tags = [t.strip() for t in raw.split(",") if t.strip()]

    # 自動タグ推薦
    auto_tags_used = []
    if not args.no_auto_tags:
        memory_dir = memory_utils.get_memory_dir(args.scope)
        corpus = similarity.load_corpus(memory_dir)
        suggested = auto_tagger.suggest_tags(title, summary, content, tags, corpus, max_tags=5)
        if suggested:
            tags, auto_tags_used = auto_tagger.merge_tags(tags, suggested, max_total=10)

    # 類似チェック
    similar_memories = []
    if not args.no_dedup:
        memory_dir = memory_utils.get_memory_dir(args.scope)
        similar_memories = similarity.find_similar_memories(
            memory_dir, title, summary, tags, threshold=args.dedup_threshold, limit=5
        )

    # 類似記憶が見つかった場合の処理
    if similar_memories:
        if args.dedup_report:
            # 非インタラクティブモード: 保存して類似一覧を出力
            pass  # 後で保存処理に進む
        else:
            # インタラクティブモード: 確認を求める
            print("\n⚠ 類似する記憶が見つかりました:\n")
            for i, sim in enumerate(similar_memories, 1):
                print(f"[{i}] {sim['title']} (類似度: {sim['similarity']:.2f})")
                print(f"    {sim['mem_id']}")
                print(f"    Summary: {sim['summary'][:80]}...")
                print()

            if sys.stdin.isatty():
                choice = input("→ 既存記憶を更新しますか？\n"
                               "  (s=保存 / u=既存を更新 / m=マージ / q=中止) > ").strip().lower()
                if choice == "q":
                    print("保存を中止しました。")
                    return
                elif choice == "u":
                    # 最も類似度の高い記憶を更新
                    target = similar_memories[0]
                    filepath = os.path.join(memory_dir, target["filepath"])
                    update_memory(filepath, summary=summary, content=content)
                    print(f"Updated: {filepath}")
                    return
                elif choice == "m":
                    # マージ保存（既存の body に追記）
                    target = similar_memories[0]
                    filepath = os.path.join(memory_dir, target["filepath"])
                    with open(filepath, "r", encoding="utf-8") as f:
                        text = f.read()
                    meta, existing_body = memory_utils.parse_frontmatter(text)
                    merged_content = existing_body.strip() + "\n\n---\n\n" + content
                    update_memory(filepath, summary=summary, content=merged_content)
                    print(f"Merged: {filepath}")
                    return
                # "s" または Enter → そのまま保存に進む

    # 保存実行
    filepath = save_memory(
        category=args.category,
        title=title,
        summary=summary,
        content=content,
        tags=tags,
        scope=args.scope,
        context=args.context,
        conclusion=args.conclusion,
    )

    # コーパスを更新
    if not args.no_dedup or not args.no_auto_tags:
        memory_dir = memory_utils.get_memory_dir(args.scope)
        meta, _ = memory_utils.parse_frontmatter(open(filepath, encoding="utf-8").read())
        mem_id = meta.get("id", "")
        if mem_id:
            similarity.update_corpus_entry(memory_dir, mem_id, title, summary, tags)

    # 結果出力
    print(f"✅ 保存しました [{args.scope}]: {filepath}")
    if auto_tags_used:
        print(f"   自動タグ追加: {', '.join(auto_tags_used)}")

    # 類似記憶がある場合はレポート
    if similar_memories and args.dedup_report:
        print("\n⚠ 類似する既存記憶:")
        for sim in similar_memories:
            print(f"  [{sim['similarity']:.2f}] {sim['mem_id']} \"{sim['title']}\"")



if __name__ == "__main__":
    main()
