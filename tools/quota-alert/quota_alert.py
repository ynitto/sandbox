"""台帳の quota_snapshot から枯渇を予測し、通知用 Markdown を生成する。

forecast JSON v1 は version（1）、source（quota-alert）、generated_at、
agents（agent_cli 順の配列）を持つ。各要素は agent_cli、used_percent、
remaining_percent、reset_at、pace_percent_per_day、predicted_exhaustion_at、
status（ok / warning / critical / unknown）、reasons（日本語文字列の配列）。
日時は UTC の ISO 8601。不明なリセット日時、ペース、枯渇予測日時は null。
"""

import argparse
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import json
import math
from pathlib import Path
import sys
import tempfile


RESET_DROP_PERCENT = 10
SEVERITY = {"ok": 0, "warning": 1, "critical": 2}
DEFAULTS = {
    "ledger_dir": "~/.agents/budget/ledger",
    "state_file": "~/.config/quota-alert/state.json",
    "min_span_hours": 3,
    "critical_remaining_percent": 10,
    "warn_remaining_percent": 30,
    "warn_days": 3,
    "cooldown_hours": 24,
    "dry_run": True,
}


def parse_time(value):
    timestamp = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    return timestamp.replace(tzinfo=timestamp.tzinfo or timezone.utc).astimezone(timezone.utc)


def iso(timestamp):
    return timestamp.isoformat().replace("+00:00", "Z") if timestamp is not None else None


def load_config(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("設定は JSON オブジェクトで指定してください")
    config = {**DEFAULTS, **config}
    for key in ("ledger_dir", "state_file"):
        if not isinstance(config[key], str) or not config[key].strip():
            raise ValueError(f"{key} は空でないパスを指定してください")
        config[key] = Path(config[key]).expanduser()
    for key in ("min_span_hours", "critical_remaining_percent", "warn_remaining_percent",
                "warn_days", "cooldown_hours"):
        value = config[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{key} は 0 以上の有限な数値を指定してください")
    if not 0 <= config["critical_remaining_percent"] <= config["warn_remaining_percent"] <= 100:
        raise ValueError("残量しきい値は 0〜100 の範囲で critical ≤ warning としてください")
    if type(config["dry_run"]) is not bool:
        raise ValueError("dry_run は true または false を指定してください")
    return config


@dataclass(frozen=True)
class Snapshot:
    timestamp: datetime
    used: float
    reset: datetime | None
    rate_limited: bool


def read_snapshots(ledger_dir, now):
    agents = defaultdict(list)
    for path in sorted(ledger_dir.glob("*.jsonl")):
        with path.open("rb") as ledger:
            for line in ledger:
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict) or row.get("event") != "quota_snapshot":
                        continue
                    agent = row["agent_cli"]
                    used = float(row["quota_used_percent"])
                    timestamp = parse_time(row["ts"])
                    if not isinstance(agent, str) or not agent.strip() or not math.isfinite(used):
                        continue
                    if used < 0 or timestamp > now:
                        continue
                except (KeyError, AttributeError, TypeError, ValueError, OverflowError):
                    continue
                try:
                    reset = parse_time(row.get("reset_at") or "")
                except (AttributeError, TypeError, ValueError, OverflowError):
                    reset = None
                agents[agent].append(Snapshot(timestamp, used, reset, row.get("quota_kind") == "rate_limit"))
    return agents


def forecast(agent, snapshots, config, now):
    snapshots.sort(key=lambda sample: sample.timestamp)
    first = previous = snapshots[0]
    for sample in snapshots[1:]:
        if (previous.used - sample.used >= RESET_DROP_PERCENT
                or previous.reset is not None and previous.timestamp < previous.reset <= sample.timestamp):
            first = sample
        previous = sample

    latest = snapshots[-1]
    remaining = max(0, 100 - latest.used)
    span_hours = (latest.timestamp - first.timestamp).total_seconds() / 3600
    pace = exhaustion = None
    if (span_hours > 0 and span_hours >= config["min_span_hours"] and latest.used > first.used
            and (latest.reset is None or now < latest.reset)):
        pace = (latest.used - first.used) / span_hours * 24
        if math.isfinite(pace) and pace > 0:
            try:
                exhaustion = latest.timestamp + timedelta(days=remaining / pace)
            except OverflowError:
                pass
        else:
            pace = None

    critical = []
    warning = []
    if latest.used >= 100:
        critical.append("使用率が上限に達しています")
    if latest.rate_limited:
        critical.append("利用制限に達しています")
    if remaining <= config["critical_remaining_percent"]:
        critical.append("残量が緊急しきい値以下です")
    elif remaining <= config["warn_remaining_percent"]:
        warning.append("残量が警告しきい値以下です")
    if exhaustion is not None:
        if latest.reset is not None and exhaustion < latest.reset:
            critical.append("リセット前に枯渇する見込みです")
        elif latest.reset is None and (exhaustion - now).total_seconds() / 86400 <= config["warn_days"]:
            warning.append("警告日数以内に枯渇する見込みです")

    if critical:
        status, reasons = "critical", critical + warning
    elif warning:
        status, reasons = "warning", warning
    elif exhaustion is None:
        status, reasons = "unknown", ["現在のウィンドウで消費ペースを予測できません"]
    else:
        status, reasons = "ok", []
    return {
        "agent_cli": agent,
        "used_percent": latest.used,
        "remaining_percent": round(remaining, 10),
        "reset_at": iso(latest.reset),
        "pace_percent_per_day": pace,
        "predicted_exhaustion_at": iso(exhaustion),
        "status": status,
        "reasons": reasons,
    }


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    file = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
    temporary = Path(file.name)
    try:
        with file:
            json.dump(data, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def locked_state(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            state = {}
        if not isinstance(state, dict):
            raise ValueError("通知状態ファイルは JSON オブジェクトである必要があります")
        yield state


def pending_alerts(alerts, state, now, cooldown_hours):
    pending = []
    for agent in alerts:
        name = agent["agent_cli"]
        status = agent["status"]
        prediction = agent["predicted_exhaustion_at"]
        signature = [name, status, prediction[:10] if prediction else None]
        previous = state.get(name)
        if previous:
            previous_status = previous["signature"][1]
            elapsed = (now - parse_time(previous["notified_at"])).total_seconds() / 3600
            if 0 <= elapsed < cooldown_hours and SEVERITY[status] <= SEVERITY[previous_status]:
                continue
        pending.append(agent)
        state[name] = {"signature": signature, "notified_at": iso(now)}
    return pending


def markdown(agents):
    lines = ["## エージェント CLI の quota アラート"]
    for agent in agents:
        name = " ".join(agent["agent_cli"].split())
        pace = agent["pace_percent_per_day"]
        pace_text = f"{pace:.2f}%/日" if pace is not None else "不明"
        lines.extend([
            "",
            f"- **{name}（{agent['status']}）**",
            f"  - 使用率 {agent['used_percent']:.2f}%、残量 {agent['remaining_percent']:.2f}%",
            f"  - 消費ペース {pace_text}、枯渇予測 {agent['predicted_exhaustion_at'] or '不明'}、"
            f"リセット {agent['reset_at'] or '不明'}",
            f"  - 理由 {'、'.join(agent['reasons'])}",
        ])
    return "\n".join(lines)


def run(args):
    args.output.unlink(missing_ok=True)
    config = load_config(args.config)
    now = parse_time(args.now) if args.now is not None else datetime.now(timezone.utc)
    snapshots = read_snapshots(config["ledger_dir"], now)
    agents = [forecast(agent, snapshots[agent], config, now) for agent in sorted(snapshots)]
    alerts = [agent for agent in agents if agent["status"] in ("warning", "critical")]
    report = {"version": 1, "source": "quota-alert", "generated_at": iso(now), "agents": agents}

    if alerts and not config["dry_run"]:
        path = config["state_file"]
        with locked_state(path) as state:
            pending = pending_alerts(alerts, state, now, config["cooldown_hours"])
            write_json(args.output, report)
            if pending:
                notification = markdown(pending)
                try:
                    write_json(path, state)
                except OSError:
                    args.output.unlink(missing_ok=True)
                    raise
                print(notification)
    else:
        write_json(args.output, report)
        if alerts:
            print("dry-run のため投稿しません\n" + markdown(alerts), file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--now", metavar="ISO8601")
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ValueError, RuntimeError, OverflowError, KeyError, TypeError, AttributeError) as error:
        print(f"quota-alert: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
