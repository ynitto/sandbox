#!/usr/bin/env python3
"""スキルdescriptionのトリガー評価スクリプト。

「このクエリでどのスキルが発動するか」を評価する。
環境に応じて2つのモードで動作する:

  [高精度モード] claude -p が使える環境（Claude Code）:
    実際に Claude を動かしてスキル選択を判定する。
    Anthropics の run_eval.py と同じ仕組み。

  [簡易モード] claude -p が使えない環境（Copilot / Kiro）:
    simulate_trigger.py と同じヒューリスティクス（バイグラム類似度）で判定する。
    実際のLLM判定より精度は低いが、傾向確認には使える。

使い方:
    # eval set JSON に対して一括テスト
    python eval_trigger.py --skill-path <SKILLS_BASE>/<skill-name> --eval-set eval.json

    # 単一クエリでテスト
    python eval_trigger.py --skill-path <SKILLS_BASE>/<skill-name> \\
        --query "スキルを作って" --expected true

    # モード確認
    python eval_trigger.py --check-env

eval set JSON 形式:
    [
      {"query": "スキルを作って", "should_trigger": true},
      {"query": "バグを直して", "should_trigger": false}
    ]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import select
import shutil
import subprocess
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
from utils import parse_skill_md


# ---------------------------------------------------------------------------
# 環境チェック
# ---------------------------------------------------------------------------

def _has_claude_cli() -> bool:
    """claude -p が使える環境（Claude Code）かどうかを判定する。"""
    return shutil.which("claude") is not None


def _find_project_root() -> Path:
    """Claude Code のプロジェクトルート（.claude/ が存在するディレクトリ）を返す。"""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / ".claude").is_dir():
            return parent
    return current


# ---------------------------------------------------------------------------
# 高精度モード: claude -p を使った実測（Claude Code 専用）
# ---------------------------------------------------------------------------

def _judge_trigger_claude_cli(
    query: str,
    skill_name: str,
    skill_description: str,
    timeout: int,
    project_root: Path,
    model: str | None,
) -> bool:
    """claude -p を使ってスキルが実際に発動するか判定する（Claude Code 専用）。"""
    unique_id = uuid.uuid4().hex[:8]
    clean_name = f"{skill_name}-trigger-{unique_id}"
    commands_dir = project_root / ".claude" / "commands"
    command_file = commands_dir / f"{clean_name}.md"

    try:
        commands_dir.mkdir(parents=True, exist_ok=True)
        # YAML ブロックスカラーで description を安全にエスケープ
        indented_desc = "\n  ".join(skill_description.split("\n"))
        command_file.write_text(
            f"---\ndescription: |\n  {indented_desc}\n---\n\n"
            f"# {skill_name}\n\nThis skill handles: {skill_description}\n"
        )

        cmd = [
            "claude", "-p", query,
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
        ]
        if model:
            cmd.extend(["--model", model])

        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(project_root),
            env=env,
        )

        triggered = False
        start = time.time()
        buffer = ""
        pending_tool = None
        accumulated_json = ""

        try:
            while time.time() - start < timeout:
                if process.poll() is not None:
                    remaining = process.stdout.read()
                    if remaining:
                        buffer += remaining.decode("utf-8", errors="replace")
                    break

                ready, _, _ = select.select([process.stdout], [], [], 1.0)
                if not ready:
                    continue

                chunk = os.read(process.stdout.fileno(), 8192)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if event.get("type") == "stream_event":
                        se = event.get("event", {})
                        se_type = se.get("type", "")
                        if se_type == "content_block_start":
                            cb = se.get("content_block", {})
                            if cb.get("type") == "tool_use":
                                tool_name = cb.get("name", "")
                                if tool_name in ("Skill", "Read"):
                                    pending_tool = tool_name
                                    accumulated_json = ""
                                else:
                                    return False
                        elif se_type == "content_block_delta" and pending_tool:
                            delta = se.get("delta", {})
                            if delta.get("type") == "input_json_delta":
                                accumulated_json += delta.get("partial_json", "")
                                if clean_name in accumulated_json:
                                    return True
                        elif se_type in ("content_block_stop", "message_stop"):
                            if pending_tool:
                                return clean_name in accumulated_json
                            if se_type == "message_stop":
                                return False

                    elif event.get("type") == "assistant":
                        message = event.get("message", {})
                        for item in message.get("content", []):
                            if item.get("type") != "tool_use":
                                continue
                            tool_name = item.get("name", "")
                            tool_input = item.get("input", {})
                            if tool_name == "Skill" and clean_name in tool_input.get("skill", ""):
                                triggered = True
                            elif tool_name == "Read" and clean_name in tool_input.get("file_path", ""):
                                triggered = True
                            return triggered

                    elif event.get("type") == "result":
                        return triggered
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

        return triggered
    finally:
        if command_file.exists():
            command_file.unlink()


# ---------------------------------------------------------------------------
# 簡易モード: バイグラム類似度（simulate_trigger.py 相当）
# ---------------------------------------------------------------------------

def _bigrams(text: str) -> set[str]:
    cleaned = re.sub(r'[\s\u3000「」『』【】（）。、・\-/\\|]', "", text)
    if len(cleaned) < 2:
        return set(cleaned)
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}


def _extract_triggers(description: str) -> list[str]:
    return re.findall(r"「([^」]+)」", description)


def _score_heuristic(query: str, description: str) -> float:
    """バイグラム + トリガーフレーズでスコアを計算する（simulate_trigger.py 相当）。"""
    if not description:
        return 0.0
    score = 0.0
    if query in description:
        score += 1.5
    triggers = _extract_triggers(description)
    best_trigger = 0.0
    for t in triggers:
        if t == query:
            best_trigger = max(best_trigger, 1.0)
        elif t in query or query in t:
            best_trigger = max(best_trigger, 0.8)
        else:
            q_set, t_set = set(query), set(t)
            union = q_set | t_set
            if union:
                best_trigger = max(best_trigger, len(q_set & t_set) / len(union) * 0.5)
    score += best_trigger
    q_bg = _bigrams(query)
    d_bg = _bigrams(description)
    if q_bg and d_bg:
        score += (len(q_bg & d_bg) / len(q_bg)) * 0.5
    return score


def _load_all_skills(skill_path: Path) -> list[dict]:
    """スキルベース内の全スキルを読み込む。"""
    skills_base = skill_path.parent
    skills = []
    if not skills_base.is_dir():
        return skills
    for entry in sorted(skills_base.iterdir()):
        sm = entry / "SKILL.md"
        if sm.is_file():
            try:
                name, description, _ = parse_skill_md(entry)
                if name and description:
                    skills.append({"name": name, "description": description})
            except (ValueError, OSError):
                pass
    return skills


def _judge_trigger_heuristic(
    query: str,
    target_skill_name: str,
    all_skills: list[dict],
) -> bool:
    """バイグラムスコアでターゲットスキルが最上位かどうかを判定する。"""
    scores = [
        (_score_heuristic(query, s["description"]), s["name"])
        for s in all_skills
    ]
    scores = [(sc, name) for sc, name in scores if sc > 0]
    if not scores:
        return False
    scores.sort(reverse=True)
    top_name = scores[0][1]
    return top_name == target_skill_name


# ---------------------------------------------------------------------------
# 一括評価
# ---------------------------------------------------------------------------

def run_eval(
    eval_set: list[dict],
    skill_name: str,
    skill_description: str,
    all_skills: list[dict],
    use_claude_cli: bool,
    project_root: Path | None = None,
    model: str | None = None,
    timeout: int = 30,
    num_workers: int = 5,
    verbose: bool = False,
) -> dict:
    """eval set 全件を評価して結果を返す。"""
    results = []

    if use_claude_cli and project_root:
        # 高精度モード: claude -p（ProcessPoolExecutor は SKILL.md 一時ファイルとの競合を避けるため ThreadPool を使用）
        with ThreadPoolExecutor(max_workers=num_workers) as ex:
            future_map = {
                ex.submit(
                    _judge_trigger_claude_cli,
                    item["query"],
                    skill_name,
                    skill_description,
                    timeout,
                    project_root,
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
                        print(f"警告: {e}", file=sys.stderr)
                    triggered = False
                _append_result(results, item, triggered, verbose)
    else:
        # 簡易モード: ヒューリスティクス（逐次処理）
        for item in eval_set:
            triggered = _judge_trigger_heuristic(item["query"], skill_name, all_skills)
            _append_result(results, item, triggered, verbose)

    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    return {
        "skill_name": skill_name,
        "mode": "claude-cli" if use_claude_cli else "heuristic",
        "results": results,
        "summary": {"total": total, "passed": passed, "failed": total - passed},
    }


def _append_result(results: list, item: dict, triggered: bool, verbose: bool) -> None:
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


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="スキルdescriptionのトリガー評価"
    )
    parser.add_argument("--skill-path", required=True,
                        help="スキルディレクトリのパス")
    parser.add_argument("--eval-set", default=None,
                        help="eval set JSON ファイルのパス")
    parser.add_argument("--query", default=None,
                        help="単一クエリ（--eval-set の代わりに使用）")
    parser.add_argument("--expected", choices=["true", "false"], default=None,
                        help="--query 使用時の期待値")
    parser.add_argument("--model", default=None,
                        help="claude -p に渡すモデル名（高精度モードのみ）")
    parser.add_argument("--timeout", type=int, default=30,
                        help="1クエリのタイムアウト秒数（デフォルト: 30）")
    parser.add_argument("--workers", type=int, default=5,
                        help="並列ワーカー数（デフォルト: 5）")
    parser.add_argument("--heuristic", action="store_true",
                        help="claude -p があっても強制的にヒューリスティクスモードを使う")
    parser.add_argument("--check-env", action="store_true",
                        help="動作環境を確認して終了")
    parser.add_argument("--verbose", action="store_true",
                        help="進捗を stderr に出力")
    args = parser.parse_args()

    # 環境確認
    has_cli = _has_claude_cli()
    use_claude_cli = has_cli and not args.heuristic

    if args.check_env:
        print(f"claude CLI: {'✅ 利用可能（高精度モード）' if has_cli else '❌ 未インストール（簡易モード）'}")
        print(f"評価モード: {'claude-cli' if use_claude_cli else 'heuristic'}")
        if not has_cli:
            print("\n簡易モード（バイグラム類似度）で動作します。")
            print("高精度モードには Claude Code のインストールが必要です。")
            print("Copilot / Kiro 環境では optimize_description.py の")
            print("エージェント駆動評価を使ってください。")
        return

    skill_path = Path(args.skill_path)
    if not (skill_path / "SKILL.md").exists():
        print(f"エラー: SKILL.md が見つかりません: {skill_path}", file=sys.stderr)
        sys.exit(1)

    name, description, _ = parse_skill_md(skill_path)
    all_skills = _load_all_skills(skill_path)
    project_root = _find_project_root() if use_claude_cli else None

    if args.verbose:
        mode_str = "claude-cli（高精度）" if use_claude_cli else "heuristic（簡易）"
        print(f"スキル: {name}  モード: {mode_str}", file=sys.stderr)

    # 単一クエリモード
    if args.query:
        if use_claude_cli:
            triggered = _judge_trigger_claude_cli(
                args.query, name, description, args.timeout, project_root, args.model
            )
        else:
            triggered = _judge_trigger_heuristic(args.query, name, all_skills)
        expected = (args.expected == "true") if args.expected else None
        did_pass = (triggered == expected) if expected is not None else None
        result = {
            "query": args.query,
            "skill_name": name,
            "triggered": triggered,
            "expected": expected,
            "pass": did_pass,
            "mode": "claude-cli" if use_claude_cli else "heuristic",
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
        skill_description=description,
        all_skills=all_skills,
        use_claude_cli=use_claude_cli,
        project_root=project_root,
        model=args.model,
        timeout=args.timeout,
        num_workers=args.workers,
        verbose=args.verbose,
    )

    summary = output["summary"]
    if args.verbose:
        print(f"\n結果: {summary['passed']}/{summary['total']} PASS  "
              f"（モード: {output['mode']}）", file=sys.stderr)
        if not use_claude_cli:
            print("※ heuristic モード: 実際のLLM判定より精度が低い場合があります",
                  file=sys.stderr)

    print(json.dumps(output, ensure_ascii=False, indent=2))
    if summary["failed"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
