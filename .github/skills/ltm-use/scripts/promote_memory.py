#!/usr/bin/env python3
"""
promote_memory.py - home 記憶を shared へ昇格するスクリプト

Usage:
  # 昇格候補を表示（share_score >= 70）
  python promote_memory.py --list

  # 半自動: 候補を確認しながら昇格 (score >= 70)
  python promote_memory.py

  # 自動昇格: score >= 85 のものを自動で昇格
  python promote_memory.py --auto

  # 特定ファイルを指定して昇格
  python promote_memory.py --file <AGENT_HOME>/memory/home/auth/jwt.md

  # home → shared へ昇格（git commit も実施）
  python promote_memory.py --target shared --auto

  # home → shared へ昇格 + push まで一括
  python promote_memory.py --target shared --auto --push
"""

import argparse
import os
import shutil
import sys

import memory_utils


def load_candidate_memories(src_scope: str, threshold: int) -> list[dict]:
    """src_scope から share_score >= threshold の記憶を返す。

    インデックスで事前フィルタリングし、候補ファイルのみ読み込むことで
    記憶数が多い場合のパフォーマンスを向上させる。
    """
    src_dir = memory_utils.get_memory_dir(src_scope)

    # インデックスから候補を事前絞り込み（全ファイル読み込みを回避）
    index = memory_utils.load_index(src_dir)
    if not index.get("entries"):
        index = memory_utils.refresh_index(src_dir)

    pre_candidates = [
        e for e in index.get("entries", [])
        if e.get("share_score", 0) >= threshold
        and e.get("status", "active") == "active"
    ]

    candidates = []
    for entry in pre_candidates:
        fpath = os.path.join(src_dir, entry["filepath"])
        if not os.path.exists(fpath):
            continue
        with open(fpath, encoding="utf-8") as f:
            text = f.read()
        meta, body = memory_utils.parse_frontmatter(text)
        score = memory_utils.compute_share_score(meta, body)
        # キャッシュと実スコアが乖離していれば更新
        if meta.get("share_score", 0) != score:
            memory_utils.update_frontmatter_fields(fpath, {"share_score": score})
            memory_utils.update_index_entry(src_dir, fpath)
        if score >= threshold:
            rel_cat = os.path.relpath(os.path.dirname(fpath), src_dir)
            candidates.append({
                "filepath": fpath,
                "rel_cat": rel_cat,
                "title": meta.get("title", os.path.basename(fpath)),
                "summary": meta.get("summary", ""),
                "score": score,
                "status": meta.get("status", "active"),
            })

    return sorted(candidates, key=lambda x: x["score"], reverse=True)


def promote_file(src_path: str, src_scope: str, target_scope: str) -> str:
    """記憶ファイルを src_scope → target_scope へコピーし、scope フィールドを更新する"""
    src_dir = memory_utils.get_memory_dir(src_scope)
    dst_dir = memory_utils.get_memory_dir(target_scope)
    rel = os.path.relpath(src_path, src_dir)
    # パストラバーサル防止: src_dir 外への書き込みを拒否
    if rel.startswith(".."):
        print(f"エラー: ファイルがスコープ外です: {src_path}", file=sys.stderr)
        sys.exit(1)
    dst_path = os.path.join(dst_dir, rel)
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    with open(src_path, encoding="utf-8") as f:
        src_meta, _ = memory_utils.parse_frontmatter(f.read())
    src_id = src_meta.get("id", "")
    shutil.copy2(src_path, dst_path)
    memory_utils.update_frontmatter_fields(dst_path, {
        "scope": target_scope,
        "promoted_from": src_id,
    })
    return dst_path


def main():
    parser = argparse.ArgumentParser(description="記憶を上位スコープへ昇格する")
    parser.add_argument("--scope", default="home",
                        choices=["home"],
                        help="昇格元スコープ (default: home)")
    parser.add_argument("--target", default="shared",
                        choices=["shared"],
                        help="昇格先スコープ (default: shared)")
    parser.add_argument("--list", action="store_true", help="昇格候補を表示して終了")
    parser.add_argument("--auto", action="store_true",
                        help="share_score >= auto_promote_threshold を自動昇格")
    parser.add_argument("--file", help="特定ファイルのパスを直接指定して昇格")
    parser.add_argument("--threshold", type=int, default=None,
                        help="昇格候補の最低スコア（省略時は config 値を使用）")
    parser.add_argument("--push", action="store_true",
                        help="shared 昇格後に git push まで自動実行（--auto と組み合わせて使用）")
    args = parser.parse_args()

    cfg = memory_utils.load_config()
    semi_threshold = args.threshold or cfg["semi_auto_promote_threshold"]
    auto_threshold = cfg["auto_promote_threshold"]

    target_scope = args.target

    # 昇格先ディレクトリを確認・作成
    dst_dir = memory_utils.get_memory_dir(target_scope)
    os.makedirs(dst_dir, exist_ok=True)

    # --- 特定ファイル指定 ---
    if args.file:
        if not os.path.exists(args.file):
            print(f"ファイルが見つかりません: {args.file}", file=sys.stderr)
            sys.exit(1)
        dst = promote_file(args.file, args.scope, target_scope)
        print(f"昇格完了: {args.file} → {dst}")
        if target_scope == "shared":
            repo = memory_utils.get_primary_writable_repo()
            if repo:
                ok, msg = memory_utils.git_commit_repo(
                    repo, f"feat: promote memory from {args.scope}"
                )
                print(f"git commit: {'OK' if ok else 'FAILED'} - {msg}")
                if ok and args.push:
                    ok2, msg2 = memory_utils.git_push_repo(repo)
                    print(f"git push: {'成功' if ok2 else '失敗'} - {msg2}")
            else:
                print("注意: 書き込み可能な共有リポジトリが設定されていません。git commit をスキップします。")
        return

    # --- 候補スキャン ---
    # --auto 時は auto_threshold でスキャン（semi_threshold の 70〜84 を読み込まない）
    scan_threshold = auto_threshold if args.auto else semi_threshold
    candidates = load_candidate_memories(args.scope, scan_threshold)

    if not candidates:
        # --auto 時は候補なしを静かにスキップ（セッション開始の自動実行でノイズにしない）
        if not args.auto:
            print(f"昇格候補がありません（score >= {scan_threshold}）")
        return

    print(f"昇格候補: {len(candidates)}件（{args.scope} → {target_scope}）\n")
    for i, c in enumerate(candidates, 1):
        auto_mark = " [AUTO]" if c["score"] >= auto_threshold else ""
        print(f"[{i}]{auto_mark} score={c['score']:2d} | {c['title']}")
        print(f"     {c['summary']}")
        print(f"     {c['filepath']}")
        print()

    if args.list:
        return

    promoted = []
    skipped = []

    for c in candidates:
        if args.auto and c["score"] >= auto_threshold:
            dst = promote_file(c["filepath"], args.scope, target_scope)
            promoted.append((c["filepath"], dst))
            print(f"[自動昇格] {c['title']} → {dst}")
        elif not args.auto:
            ans = input(f"「{c['title']}」(score={c['score']}) を昇格しますか？ [y/N/q] ").strip().lower()
            if ans == "q":
                print("中断しました。")
                break
            elif ans == "y":
                dst = promote_file(c["filepath"], args.scope, target_scope)
                promoted.append((c["filepath"], dst))
                print(f"  → 昇格: {dst}")
            else:
                skipped.append(c["title"])

    print(f"\n結果: 昇格 {len(promoted)}件 / スキップ {len(skipped)}件")

    if promoted and target_scope == "shared":
        repo = memory_utils.get_primary_writable_repo()
        if not repo:
            print("注意: 書き込み可能な共有リポジトリが設定されていません。git commit をスキップします。")
        else:
            ans = "y" if args.auto else input("git commit しますか？ [Y/n] ").strip().lower()
            if ans != "n":
                ok, msg = memory_utils.git_commit_repo(
                    repo,
                    f"feat: promote {len(promoted)} memories from {args.scope}"
                )
                print(f"git commit: {'OK' if ok else 'FAILED'} - {msg}")
                if ok:
                    if args.push:
                        ok2, msg2 = memory_utils.git_push_repo(repo)
                        print(f"git push: {'成功' if ok2 else '失敗'} - {msg2}")
                    else:
                        print(f"共有するには: python sync_memory.py --push --repo {repo['name']}")


if __name__ == "__main__":
    main()
