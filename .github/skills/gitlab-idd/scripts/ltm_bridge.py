"""Learning-loop records, validation, and local skill execution."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


MARKER = re.compile(r"<!--\s*gitlab-idd:ltm:v1\s+(\{.*?\})\s*-->", re.DOTALL)
CAUSE = re.compile(r"<!--\s*gitlab-idd:rework-cause:([a-z\-,]+)\s*-->")
APPROVAL = re.compile(r"<!--\s*gitlab-idd:requester-approved:[^\s<>]+\s*-->")
RATED_RESULTS = {"ok", "not-found"}
CORRECTION_CAUSES = {"spec-misread", "wrong-approach", "quality", "env"}
STEPS = ("recall", "save", "retro", "rate", "sprint-review")
SAVE_KEYS = {
    "category", "title", "summary", "content", "conclusion", "tags", "context",
    "memory_type", "importance",
}


class InvalidInput(ValueError):
    pass


def marker(event):
    payload = json.dumps(event, ensure_ascii=True, separators=(",", ":"))
    payload = payload.replace("--", r"\u002d\u002d").replace(">", r"\u003e")
    return f"<!-- gitlab-idd:ltm:v1 {payload} -->"


def parse_markers(body):
    events, malformed = [], 0
    for match in MARKER.finditer(body):
        try:
            events.append(json.loads(match[1]))
        except ValueError:
            malformed += 1
    return events, malformed


class History:
    def __init__(self, notes, node, state="opened"):
        self.node = node
        self.state = state
        self.events = []
        self.reworks = []
        self.causes = []
        self.approvals = []
        self.malformed = 0
        self.via_clarification = False
        self.cycle = 1
        for position, note in enumerate(notes):
            body = note["body"]
            self.via_clarification |= "gitlab-idd:clarification-requested" in body
            if APPROVAL.search(body):
                self.approvals.append((self.cycle, position))
            events, malformed = parse_markers(body)
            self.events.extend((position, event) for event in events)
            self.malformed += malformed
            causes = CAUSE.findall(body)
            if causes or body.startswith("## ❌ 差し戻し"):
                self.reworks.append(body)
                self.causes.append(list(dict.fromkeys(
                    slug for group in causes for slug in group.split(",") if slug
                )) or ["unknown"])
                self.cycle += 1

    def find(self, kind, cycle, *, any_node=False, cycle_key="cycle"):
        return [
            (position, event) for position, event in self.events
            if event.get("v") == 1 and event.get("event") == kind
            and event.get(cycle_key) == cycle
            and (any_node or event.get("node") == self.node)
        ]

    def latest(self, kind, cycle):
        found = self.find(kind, cycle)
        return found[-1][1] if found else {}

    def outcome(self, cycle):
        if cycle < self.cycle:
            return "rework"
        usage = self.find("usage", cycle)
        if usage and any(
            number == cycle and position > usage[-1][0]
            for number, position in self.approvals
        ):
            return "approved"
        return "closed-unknown" if self.state == "closed" else "pending"

    def ratings(self, cycle):
        return self.find("rate", cycle, cycle_key="target_cycle")

    def eligible(self, cycle):
        recalled = {row["id"] for row in self.latest("recall", cycle).get("memories", [])}
        return recalled & applied_ids(self.latest("usage", cycle))

    def rated_ids(self, cycle):
        return {row["id"] for _, event in self.ratings(cycle)
                for row in event.get("applied", []) if row.get("result") in RATED_RESULTS}

    def rate_complete(self, cycle):
        accounted_for = self.rated_ids(cycle)
        for _, event in self.ratings(cycle):
            accounted_for.update(row["id"] for row in event.get("held", []))
            accounted_for.update(row["id"] for row in event.get("skipped", [])
                                 if row.get("reason") not in ("already-rated", "unavailable"))
        return self.eligible(cycle) <= accounted_for

    def trail(self, issue_id):
        cycles, retros, rates, failures = [], [], [], []
        for cycle in range(1, self.cycle + 1):
            outcome = self.outcome(cycle)
            retro = any(
                event.get("trigger") == "rework" for _, event in
                self.find("retro", cycle, any_node=True, cycle_key="target_cycle")
            )
            usage = bool(self.find("usage", cycle))
            rated = usage and self.rate_complete(cycle)
            cycles.append(dict(cycle=cycle, recall=bool(self.find("recall", cycle)),
                               usage=usage, outcome=outcome, rated=rated, retro=retro))
            if outcome == "rework" and not retro:
                retros.append(cycle)
            if outcome in ("rework", "approved") and usage and not rated:
                rates.append(dict(cycle=cycle, outcome=outcome))

        for index, (_, event) in enumerate(self.events):
            if event.get("v") != 1 or event.get("event") != "failure":
                continue
            step = event.get("step")
            resolved = step != "sprint-review" and any(
                self.resolves_failure(later, event)
                for _, later in self.events[index + 1:]
            )
            if not resolved:
                failures.append({key: event[key] for key in ("step", "cycle", "at")
                                 if key in event})
        return dict(issue_id=issue_id, node=self.node, cycle=self.cycle,
                    rework_count=len(self.reworks), rework_causes=self.causes,
                    via_clarification=self.via_clarification, cycles=cycles,
                    pending=dict(recall=not cycles[-1]["recall"], retro_cycles=retros,
                                 rate=rates),
                    open_failures=failures, malformed=self.malformed)

    @staticmethod
    def resolves_failure(event, failure):
        if (event.get("v") != 1 or event.get("event") != failure.get("step")
                or event.get("cycle") != failure.get("cycle")
                or event.get("node") != failure.get("node")):
            return False
        if event["event"] == "recall":
            return event.get("status") in ("ok", "empty")
        if event["event"] == "rate":
            return all(item.get("result") != "error" for item in event.get("applied", []))
        return True

    def retro_check(self, done_count):
        reasons = []
        if done_count > 0 and done_count % 10 == 0:
            reasons.append("done-multiple-of-10")
        if len(self.reworks) >= 2:
            reasons.append("rework>=2")
        if self.via_clarification:
            reasons.append("via-clarification")
        return dict(due=bool(reasons), reasons=reasons, done_count=done_count)


def make_event(history, kind, **fields):
    return dict(v=1, event=kind, node=history.node, cycle=history.cycle,
                at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                **fields)


@dataclass
class Run:
    code: int | None = None
    attempts: int = 0
    stdout: str = ""
    stderr: str = ""

    @property
    def not_found(self):
        return self.code == 1 and "ID が見つかりません" in self.stderr


def script_path(name, skill="ltm"):
    override = os.environ.get(f"GITLAB_IDD_{skill.upper()}_SCRIPTS")
    root = (Path(override) if override is not None else
            Path(__file__).resolve().parents[2] / f"{skill}-use" / "scripts")
    return root / name


def run_script(path, argv, *, rating=False, decode=None):
    if not path.is_file():
        return Run(), None
    for attempts in (1, 2):
        try:
            proc = subprocess.run(
                [sys.executable, str(path), *argv], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120,
                # 出力は UTF-8 として読むので、子の標準入出力も UTF-8 に固定する。
                # Windows では既定がロケールの文字コードになり、日本語や絵文字を
                # 出力した時点で子が UnicodeEncodeError で落ちてしまう。
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            result = Run(proc.returncode, attempts, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as exc:
            stderr = exc.stderr or ""
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            result = Run(1, attempts, stderr=f"{exc}\n{stderr}".rstrip())
        except OSError as exc:
            result = Run(1, attempts, stderr=str(exc))
        if result.code == 0:
            try:
                return result, decode(result.stdout) if decode else None
            except (ValueError, TypeError, KeyError) as exc:
                result.code = 1
                result.stderr = f"{result.stderr}\n{exc}".strip()
        if rating and result.not_found:
            break
        if attempts == 1:
            time.sleep(2)
    return result, None


def search_rows(stdout, *, keep_summary):
    rows = []
    for item in json.loads(stdout)["results"]:
        if not item["id"]:
            continue
        row = dict(id=item["id"], title=item["title"], score=float(item["score"]))
        if keep_summary and "summary" in item:
            row["summary"] = item["summary"]
        rows.append(row)
    return rows


def search(path, queries, argv, limit, *, keep_summary=False):
    results = {}
    failures = []
    succeeded = False
    for query in queries:
        result, rows = run_script(
            path, argv(query), decode=lambda output: search_rows(
                output, keep_summary=keep_summary
            ),
        )
        if result.code is None:
            return "unavailable", [], []
        if result.code:
            failures.append((query, result))
            continue
        succeeded = True
        for row in rows:
            old = results.get(row["id"])
            if old is None or row["score"] > old["score"]:
                results[row["id"]] = row
    ordered = sorted(results.values(), key=lambda row: row["score"], reverse=True)[:limit]
    status = "ok" if ordered else "empty"
    if failures and not succeeded:
        status = "error"
    return status, ordered, failures


def knowledge(event, history):
    output = {key: event[key] for key in ("status", "cycle", "trigger", "wiki_status")}
    for key in ("memories", "wiki"):
        output[key] = [
            {**{k: v for k, v in row.items() if k != "score"}, "relevance": row["score"]}
            for row in event[key]
        ]
    if "failed_queries" in event:
        output["failed_queries"] = event["failed_queries"]
    if event["trigger"] == "rework":
        output["rework_causes"] = history.causes[-1]
    return output


def table_cell(value):
    # Prevent child output or free text from introducing HTML comment markers.
    return str(value).replace("<", "&lt;").replace("|", r"\|").replace("\r", "").replace("\n", " ")


def comment(text, event):
    return f"{text}\n\n{marker(event)}"


def failure_comment(history, step, run, lost=None):
    tail = run.stderr[-500:]
    data = dict(step=step, exit_code=run.code, attempts=run.attempts, stderr_tail=tail)
    lines = ["## ⚠️ 学習ループの失敗", f"{step} (exit {run.code}, attempts {run.attempts})"]
    if tail:
        lines.append(tail.replace("<!--", "&lt;!--"))
    if lost is not None:
        data["lost"] = lost
        lines.extend(table_cell(value) for value in lost.values())
    return comment("\n\n".join(lines), make_event(history, "failure", **data))


def load_object(path):
    if not path:
        raise InvalidInput("JSON ファイルを指定してください")
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise InvalidInput(str(exc)) from exc
    if not isinstance(data, dict):
        raise InvalidInput("JSON オブジェクトを指定してください")
    return data


def read_items(data, field):
    items = data.get(field)
    if not isinstance(items, list) or any(
        not isinstance(item, dict) or not nonempty(item, "id") for item in items
    ):
        raise InvalidInput(f"{field} には ID を持つ項目の配列を指定してください")
    return items


def nonempty(item, key):
    value = item.get(key)
    return isinstance(value, str) and bool(value.strip())


def validate_usage(items, recall=None):
    if recall is not None and not recall:
        raise InvalidInput("先に ltm-recall を実行してください")
    known = {row["id"] for row in recall["memories"]} if recall is not None else set()
    usage = {}
    for item in items:
        mid, use = item["id"], item.get("use")
        if recall is not None and mid not in known:
            raise InvalidInput(f"recall に無い ID: {mid}")
        if use not in ("applied", "referenced", "unused"):
            raise InvalidInput(f"{mid}: use が不正です")
        if use == "applied" and not nonempty(item, "how"):
            raise InvalidInput(f"{mid}: applied には how が必要です")
        usage[mid] = dict(id=mid, use=use, how=item.get("how", ""))
    for mid in sorted(known - usage.keys()):
        usage[mid] = dict(id=mid, use="unused", how="")
    return list(usage.values())


def validate_save(data):
    if data.keys() - SAVE_KEYS or not all(isinstance(value, str) for value in data.values()):
        raise InvalidInput("保存用 JSON に不正なキーまたは値があります")
    if not all(nonempty(data, key) for key in ("title", "summary")):
        raise InvalidInput("title と summary は必須です")


def evidence_quotes(item):
    if not nonempty(item, "evidence"):
        return []
    matches = re.findall(r'「([^」]+)」|『([^』]+)』|"([^"]+)"', item["evidence"])
    return [part for match in matches for part in match if len(part.strip()) >= 4]


def validate_rate(items, outcome, history=None, target=None):
    """Validate the complete plan before letting any item reach the subprocess."""
    errors, seen, actions, held, skipped = [], set(), [], [], []
    previous, corrected = set(), set()
    eligible = set()
    correction_used = False
    if history is not None:
        eligible = history.eligible(target)
        previous = history.rated_ids(target)
        for _, event in history.events:
            if event.get("v") != 1 or event.get("event") != "rate":
                continue
            for row in event.get("applied", []):
                if row["rating"] != "correction":
                    continue
                if row.get("result") == "ok":
                    corrected.add(row["id"])
                if event.get("target_cycle") == target and row.get("result") in RATED_RESULTS:
                    correction_used = True
    for item in items:
        mid, rating = item["id"], item.get("rating")
        if mid in seen:
            errors.append(f"{mid}: ID が重複しています")
        seen.add(mid)
        if rating not in ("good", "correction", "hold", "none"):
            errors.append(f"{mid}: rating が不正です")
            continue
        if rating in ("hold", "none"):
            if not nonempty(item, "reason"):
                errors.append(f"{mid}: {rating} には reason が必要です")
            else:
                (held if rating == "hold" else skipped).append(
                    dict(id=mid, reason=item["reason"])
                )
            continue
        if "note" in item and not isinstance(item["note"], str):
            errors.append(f"{mid}: note には文字列を指定してください")
        if history is not None and mid not in eligible:
            errors.append(f"{mid}: 自ノードで recall と applied が必要です")
        if rating == "good" and outcome != "approved":
            errors.append(f"{mid}: good には承認が必要です")
        if mid in previous:
            skipped.append(dict(id=mid, reason="already-rated"))
            continue
        if rating == "correction":
            if outcome != "rework":
                errors.append(f"{mid}: correction には差し戻しが必要です")
            if history is not None and outcome == "rework":
                if not CORRECTION_CAUSES.intersection(history.causes[target - 1]):
                    errors.append(f"{mid}: correction を許可する原因分類がありません")
            quotes = evidence_quotes(item)
            if not quotes or (
                history is not None and outcome == "rework"
                and not all(quote in history.reworks[target - 1] for quote in quotes)
            ):
                errors.append(f"{mid}: evidence に差し戻しコメントの引用が必要です")
            if not nonempty(item, "note"):
                errors.append(f"{mid}: correction には note が必要です")
            if mid in corrected:
                held.append(dict(id=mid, reason="このイシューで correction 済みです"))
                continue
            if correction_used:
                held.append(dict(id=mid, reason="1 回の差し戻しにつき correction は 1 件までです"))
                continue
            correction_used = True
        actions.append(dict(id=mid, rating=rating, note=item.get("note", "")))
    missing = eligible - seen
    if missing:
        errors.append("評価計画に含まれていない applied の ID: " + ", ".join(sorted(missing)))
    if errors:
        raise InvalidInput("\n".join(errors))
    return actions, held, skipped


def applied_ids(usage):
    return {row["id"] for row in usage.get("memories", []) if row["use"] == "applied"}


@dataclass
class Result:
    output: dict | None = None
    comments: tuple = ()
    failure: str | None = None


def recall_comment(event, wiki_error=None):
    lines = ["## 📚 参照した知見"]
    if event["status"] == "unavailable":
        lines.append("記憶の検索は未導入のため行っていません")
    elif event["memories"]:
        lines.extend(["| 記憶 ID | タイトル | 関連度 |", "|---|---|---|"])
        for row in event["memories"]:
            lines.append(f"| {table_cell(row['id'])} | {table_cell(row['title'])} | {row['score']} |")
    elif event["status"] == "empty":
        lines.append("記憶: 該当なし")
    if event["wiki"]:
        lines.extend(["", "| wiki ページ | 関連度 |", "|---|---|"])
        for row in event["wiki"]:
            lines.append(f"| {table_cell(row['id'])} {table_cell(row['title'])} | {row['score']} |")
    elif event["wiki_status"] == "empty":
        lines.append("wiki: 該当なし")
    if wiki_error:
        lines.append(f"wiki の検索に失敗しました: {table_cell(wiki_error.stderr[-500:] or wiki_error.code)}")
    return comment("\n".join(lines), event)


def option_value(flag, value):
    if value.startswith("-"):
        return [f"{flag}={value}"]
    return [flag, value]


def recall(args, read_history):
    if len(args.query) > 3 or len(args.wiki_query) > 3 or not 1 <= args.limit <= 10:
        raise InvalidInput("クエリは各 3 件まで、limit は 1〜10 を指定してください")
    history = read_history()
    trigger = "rework" if history.reworks else "start"
    existing = history.latest("recall", history.cycle)
    if existing and not args.force:
        return Result(knowledge(existing, history))

    if args.dry_run:
        return Result(comments=("## 📚 参照した知見\n\n検索語: "
                                + ", ".join(map(table_cell, args.query)),))

    def recall_args(query):
        options = ["--scope", "all", "--limit", str(args.limit), "--json", "--no-track"]
        if args.context is not None:
            options.extend(option_value("--context", args.context))
        return [*options, "--", query] if query.startswith("-") else [query, *options]

    status, memories, failures = search(
        script_path("recall_memory.py"), args.query, recall_args, args.limit, keep_summary=True,
    )
    wiki_status, wiki, wiki_failures = search(
        script_path("wiki_query.py", "wiki"), args.wiki_query,
        lambda query: ["search", "--json", "--", query] if query.startswith("-")
        else ["search", query, "--json"],
        5,
    )
    wiki_error = wiki_failures[-1][1] if wiki_failures else None
    if wiki_error:
        wiki_status = "error"
    event = make_event(history, "recall", trigger=trigger, queries=args.query,
                       status=status, memories=memories, wiki_status=wiki_status, wiki=wiki)
    if failures:
        event["failed_queries"] = [query for query, _ in failures]
    return Result(knowledge(event, history), (recall_comment(event, wiki_error),),
                  failure_comment(history, "recall", failures[-1][1]) if status == "error" else None)


def log(args, read_history):
    if args.event == "failure":
        if args.data_file is not None or args.step is None or not args.reason:
            raise InvalidInput("failure には --step と --reason が必要です")
        body = failure_comment(read_history(), args.step, Run(1, 1, stderr=args.reason))
        return Result(comments=(body,))

    if args.step is not None or args.reason is not None:
        raise InvalidInput("--step と --reason は failure 専用です")
    items = read_items(load_object(args.data_file), "memories")
    history = read_history()
    recall_event = None if args.dry_run else history.latest("recall", history.cycle)
    memories = validate_usage(items, recall_event)
    event = make_event(history, "usage", memories=memories)
    ids = [row["id"] for row in memories if row["use"] == "applied"]
    if ids and not args.dry_run:
        run_script(script_path("recall_memory.py"), [
            *option_value("--ids", ",".join(ids)), "--json",
        ])
    return Result(event, (comment("📎 参照した知見の使い方を記録しました。", event),))


def saved_filename(output):
    match = re.search(r"保存しました \[[^\]\n]+\]:[ \t]*(.+)", output)
    if not match or not match[1].strip():
        raise ValueError("保存先のファイル名を読み取れませんでした")
    return Path(match[1].strip()).name


def save(args, read_history):
    data = load_object(args.args_file)
    validate_save(data)
    retro = args.kind == "retro"
    if retro and args.trigger is None:
        raise InvalidInput("retro には --trigger が必要です")

    history = read_history()
    kind = "retro" if retro else "save"
    heading = "## 🔁 振り返り" if retro else "🧠 実行知見を保存しました。"
    learning = data.get("conclusion", "")
    if args.dry_run:
        return Result(comments=(f"{heading}\n{table_cell(learning)}" if retro else heading,))

    fields = {}
    if retro:
        target = history.cycle
        causes = ["unknown"]
        if args.trigger == "rework":
            pending = history.trail(args.issue_id)["pending"]["retro_cycles"]
            if not pending:
                raise InvalidInput("振り返りが済んでいない差し戻しはありません")
            target = pending[0]
            causes = history.causes[target - 1]
        fields.update(trigger=args.trigger, target_cycle=target, causes=causes,
                      learning=learning)
        heading += f"\n原因分類: {', '.join(causes)}\n学び: {table_cell(learning)}"

    argv = ["--non-interactive", "--no-dedup"]
    for key, value in data.items():
        argv.extend(option_value("--" + key.replace("_", "-"), value))
    run, filename = run_script(script_path("save_memory.py"), argv, decode=saved_filename)
    if run.code is None:
        return Result(dict(status="unavailable"))
    if run.code:
        lost = dict(title=data["title"], conclusion=learning)
        return Result(dict(status="failed", file="", attempts=run.attempts),
                      failure=failure_comment(history, kind, run, lost))
    event = make_event(history, kind, file=filename, **fields)
    return Result(dict(status="ok", file=filename, attempts=run.attempts),
                  (comment(heading, event),))


def rate_comment(history, issue_id, event, path):
    label = "承認" if event["outcome"] == "approved" else "差し戻し"
    cycle = event["target_cycle"]
    lines = [f"## ⚖️ 記憶への評価返し（{label}・サイクル {cycle or '—'}）", "",
             "| 記憶 | 評価 | 理由 |", "|---|---|---|"]
    titles = {row["id"]: row["title"]
              for row in history.latest("recall", cycle).get("memories", [])}
    labels = {"good": "良い", "correction": "要修正"}
    for item in event["applied"]:
        mid = item["id"]
        rating = labels[item["rating"]]
        if item.get("result") not in (None, "ok"):
            rating += f" ({item['result']})"
        lines.append(f"| {table_cell(mid)} {table_cell(titles.get(mid, ''))} | "
                     f"{rating} | {table_cell(item['note'])} |")
    for group, label in (("held", "保留"), ("skipped", "なし")):
        for item in event[group]:
            lines.append(f"| {table_cell(item['id'])} | {label} | {table_cell(item['reason'])} |")
    lines.extend([
        "", f"評価を返したノード: `{table_cell(history.node)}`。"
        "誤りだと思ったら、このコメントに返信してください（取り消し手順は下）。",
        "", "<details><summary>取り消し方</summary>", "", "上のノードで実行します。",
    ])
    for item in event["applied"]:
        if item["rating"] == "correction" and item.get("result") == "ok":
            lines.append(f'`python "{table_cell(path)}" --id {table_cell(item["id"])} '
                         f'--good --note "取り消し: #{issue_id} の評価は誤判定"`')
    lines.extend(["correction は --good で評価点を戻せます。修正回数は残ります。", "</details>"])
    return comment("\n".join(lines), event)


def rate_target(history, outcome, items):
    if outcome == "approved":
        if history.outcome(history.cycle) == "approved":
            return history.cycle
        return None
    reworked = [cycle for cycle in range(1, history.cycle)
                if history.find("usage", cycle)]
    for cycle in reworked:
        if not history.rate_complete(cycle):
            return cycle
    # An exact replay can report already-rated without executing or posting again.
    requested = {item["id"] for item in items if item.get("rating") in ("good", "correction")}
    if requested:
        for cycle in reversed(reworked):
            if requested <= history.rated_ids(cycle):
                return cycle
    return None


def rate(args, read_history):
    if args.auto:
        if args.plan_file is not None or args.outcome != "approved":
            raise InvalidInput("--auto には --outcome approved を指定してください")
        outcome, items = "approved", []
    else:
        if args.outcome is not None:
            raise InvalidInput("--outcome は --auto 専用です")
        plan = load_object(args.plan_file)
        outcome = plan.get("outcome")
        if outcome not in ("approved", "rework"):
            raise InvalidInput("outcome は approved または rework を指定してください")
        items = read_items(plan, "items")
    path = script_path("rate_memory.py")
    history = read_history()
    if args.dry_run:
        applied, held, skipped = validate_rate(items, outcome)
        event = make_event(history, "rate", outcome=outcome, target_cycle=None,
                           applied=applied, held=held, skipped=skipped)
        return Result(comments=(rate_comment(history, args.issue_id, event, path),))

    target = rate_target(history, outcome, items)
    if target is None:
        validate_rate(items, outcome)
        return Result(dict(status="nothing-to-rate"))
    if args.auto:
        applied = applied_ids(history.latest("usage", target))
        if not applied:
            return Result(dict(status="nothing-to-rate"))
        items = [dict(id=mid, rating="good") for mid in sorted(applied)]
    actions, held, skipped = validate_rate(items, outcome, history, target)
    if history.rate_complete(target) and history.ratings(target) and not actions:
        event = make_event(history, "rate", outcome=outcome, target_cycle=target,
                           applied=[], held=held, skipped=skipped)
        return Result(event)

    applied, failed = [], None
    for item in actions:
        argv = [*option_value("--id", item["id"]), "--scope", "all", "--" + item["rating"]]
        if item["rating"] == "correction":
            argv.extend(["--note", f"[gitlab-idd #{args.issue_id}] {item['note']}"])
        run, _ = run_script(path, argv, rating=True)
        if run.code is None:
            skipped.append(dict(id=item["id"], reason="unavailable"))
            continue
        result = "ok" if run.code == 0 else "not-found" if run.not_found else "error"
        if result == "error":
            failed = run
        applied.append(dict(item, result=result))
    event = make_event(history, "rate", outcome=outcome, target_cycle=target,
                       applied=applied, held=held, skipped=skipped)
    return Result(event, (rate_comment(history, args.issue_id, event, path),),
                  failure_comment(history, "rate", failed) if failed else None)
