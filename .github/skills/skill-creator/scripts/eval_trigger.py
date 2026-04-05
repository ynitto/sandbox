#!/usr/bin/env python3
"""スキルdescriptionのトリガー評価スクリプト。

Anthropic API を使って「このクエリでどのスキルが発動するか」を判定する。
claude -p（Claude Code CLI）不要。Claude Code / Copilot / Kiro すべてで動作。

必要な環境変数:
    ANTHROPIC_API_KEY: Anthropic API キー

使い方:
    # eval set JSON に対して一括テスト
    python eval_trigger.py --skill-path <SKILLS_BASE>/<skill-name> --eval-set eval.json

    # 単一クエリでテスト
    python eval_trigger.py --skill-path <SKILLS_BASE>/<skill-name> \\
        --query "スキルを作って" --expected true

    # スキルベース全体を対象に発動スキルを確認（デバッグ用）
    python eval_trigger.py --skill-path <SKILLS_BASE>/<skill-name> \\
        --query "スキルを作って" --show-all

eval set JSON 形式:
    [
      {"query": "スキルを作って", "should_trigger": true},
      {"query": "バグを直して", "should_trigger": false}
    ]

出力 JSON 形式:
    {
      "skill_name": "skill-creator",
      "description": "...",
      "results": [
        {
          "query": "スキルを作って",
          "should_trigger": true,
          "triggered": true,
          "pass": true
        }
      ],
      "summary": {"total": 20, "passed": 18, "failed": 2}
    }
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# スクリプトと同じ scripts/ ディレクトリの utils.py を参照する
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
from utils import parse_skill_md


# ---------------------------------------------------------------------------
# Anthropic API 呼び出し
# ---------------------------------------------------------------------------

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"  # 高速・低コスト（評価用）


def _call_anthropic(
    messages: list[dict],
    system: str,
    model: str,
    api_key: str,
    max_tokens: int = 64,
) -> str:
    """Anthropic Messages API を呼び出してテキストを返す（標準ライブラリのみ使用）。"""
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=data,
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Anthropic API エラー {e.code}: {detail}") from e

    # テキストブロックを抽出
    for block in body.get("content", []):
        if block.get("type") == "text":
            return block["text"].strip()
    return ""


# ---------------------------------------------------------------------------
# スキル読み込み
# ---------------------------------------------------------------------------

def _load_skills_in_dir(base: Path) -> list[dict]:
    """ディレクトリ内のスキルを読み込んで [{name, description}] を返す。"""
    skills = []
    if not base.is_dir():
        return skills
    for entry in sorted(base.iterdir()):
        skill_md = entry / "SKILL.md"
        if skill_md.is_file():
            try:
                name, description, _ = parse_skill_md(entry)
                if name and description:
                    skills.append({"name": name, "description": description})
            except (ValueError, OSError):
                pass
    return skills


def load_all_skills(skill_path: Path) -> list[dict]:
    """スキルベース（skill_path の親ディレクトリ）内のスキルを全件読み込む。"""
    skills_base = skill_path.parent
    return _load_skills_in_dir(skills_base)


# ---------------------------------------------------------------------------
# トリガー判定（1クエリ）
# ---------------------------------------------------------------------------

SYSTEM_TEMPLATE = """\
あなたはAIエージェントです。ユーザーのリクエストを受け取り、以下の利用可能なスキル（skill）の中から
最も適切なものを1つ選んで使用します。どのスキルも適切でない場合は "none" を選びます。

利用可能なスキル:
{skills_list}

ユーザーのリクエストに対して、使用すべきスキル名だけを1単語で答えてください（none も可）。
余計な説明は不要です。"""


def judge_trigger(
    query: str,
    target_skill_name: str,
    all_skills: list[dict],
    api_key: str,
    model: str,
) -> bool:
    """クエリに対してターゲットスキルが発動するかどうかを判定する。"""
    skills_list = "\n".join(
        f"- {s['name']}: {s['description']}" for s in all_skills
    )
    system = SYSTEM_TEMPLATE.format(skills_list=skills_list)
    messages = [{"role": "user", "content": query}]

    answer = _call_anthropic(
        messages=messages,
        system=system,
        model=model,
        api_key=api_key,
        max_tokens=32,
    )

    # スキル名が応答に含まれるかで判定（大文字小文字無視）
    return target_skill_name.lower() in answer.lower()


# ---------------------------------------------------------------------------
# 一括評価
# ---------------------------------------------------------------------------

def run_eval(
    eval_set: list[dict],
    skill_name: str,
    all_skills: list[dict],
    api_key: str,
    model: str,
    num_workers: int = 5,
    verbose: bool = False,
) -> dict:
    """eval set 全件を並列評価して結果を返す。"""
    results = []

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        future_map = {
            executor.submit(
                judge_trigger,
                item["query"],
                skill_name,
                all_skills,
                api_key,
                model,
            ): item
            for item in eval_set
        }
        for future in as_completed(future_map):
            item = future_map[future]
            try:
                triggered = future.result()
            except Exception as e:
                if verbose:
                    print(f"警告: クエリ評価失敗: {e}", file=sys.stderr)
                triggered = False

            did_pass = triggered == item["should_trigger"]
            results.append({
                "query": item["query"],
                "should_trigger": item["should_trigger"],
                "triggered": triggered,
                "pass": did_pass,
            })
            if verbose:
                status = "PASS" if did_pass else "FAIL"
                mark = "✓" if triggered else "✗"
                print(
                    f"  [{status}] triggered={mark} expected={item['should_trigger']}: "
                    f"{item['query'][:60]}",
                    file=sys.stderr,
                )

    passed = sum(1 for r in results if r["pass"])
    total = len(results)

    return {
        "skill_name": skill_name,
        "results": results,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
        },
    }


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="スキルdescriptionのトリガー評価（Anthropic API使用）"
    )
    parser.add_argument(
        "--skill-path",
        required=True,
        help="スキルディレクトリのパス（例: <SKILLS_BASE>/skill-creator）",
    )
    parser.add_argument(
        "--eval-set",
        default=None,
        help="eval set JSON ファイルのパス",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="単一クエリ（--eval-set の代わりに使用）",
    )
    parser.add_argument(
        "--expected",
        choices=["true", "false"],
        default=None,
        help="--query 使用時の期待値（true=発動すべき / false=発動しないべき）",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"使用するモデル（デフォルト: {DEFAULT_MODEL}）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=5,
        help="並列ワーカー数（デフォルト: 5）",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="クエリに対して全スキルの発動判定結果を表示（デバッグ用）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="進捗を stderr に出力",
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("エラー: ANTHROPIC_API_KEY 環境変数が未設定です", file=sys.stderr)
        sys.exit(1)

    skill_path = Path(args.skill_path)
    if not (skill_path / "SKILL.md").exists():
        print(f"エラー: SKILL.md が見つかりません: {skill_path}", file=sys.stderr)
        sys.exit(1)

    name, description, _ = parse_skill_md(skill_path)
    all_skills = load_all_skills(skill_path)

    if args.verbose:
        print(f"スキル: {name}", file=sys.stderr)
        print(f"対象スキル数: {len(all_skills)}", file=sys.stderr)

    # --show-all: デバッグ用（全スキルの発動判定を表示）
    if args.show_all and args.query:
        print(f"\nクエリ: {args.query}")
        skills_list = "\n".join(
            f"- {s['name']}: {s['description']}" for s in all_skills
        )
        system = SYSTEM_TEMPLATE.format(skills_list=skills_list)
        answer = _call_anthropic(
            messages=[{"role": "user", "content": args.query}],
            system=system,
            model=args.model,
            api_key=api_key,
            max_tokens=32,
        )
        print(f"発動スキル: {answer}")
        return

    # 単一クエリモード
    if args.query:
        triggered = judge_trigger(
            args.query, name, all_skills, api_key, args.model
        )
        expected = args.expected == "true" if args.expected else None
        did_pass = (triggered == expected) if expected is not None else None
        result = {
            "query": args.query,
            "skill_name": name,
            "triggered": triggered,
            "expected": expected,
            "pass": did_pass,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if did_pass is False:
            sys.exit(1)
        return

    # eval set モード
    if not args.eval_set:
        parser.error("--eval-set または --query を指定してください")

    eval_set = json.loads(Path(args.eval_set).read_text(encoding="utf-8"))
    if args.verbose:
        print(f"評価件数: {len(eval_set)}", file=sys.stderr)

    output = run_eval(
        eval_set=eval_set,
        skill_name=name,
        all_skills=all_skills,
        api_key=api_key,
        model=args.model,
        num_workers=args.workers,
        verbose=args.verbose,
    )

    summary = output["summary"]
    if args.verbose:
        print(
            f"\n結果: {summary['passed']}/{summary['total']} PASS",
            file=sys.stderr,
        )

    print(json.dumps(output, ensure_ascii=False, indent=2))
    if summary["failed"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
