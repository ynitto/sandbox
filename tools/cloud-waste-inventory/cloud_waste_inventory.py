"""クラウドの一覧を読み取り、月額の削減候補を GitLab ハブ issue に報告する。"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, DecimalException
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class InventoryError(Exception):
    pass


@dataclass(frozen=True)
class Resource:
    identity: str
    sku: str
    state: str


@dataclass(frozen=True)
class Candidate:
    resource: Resource
    reason: str
    savings: Decimal | None


def field(row, key):
    value = row[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(key)
    return value


def azure_vm(row):
    return Resource(field(row, "id"), field(row["hardwareProfile"], "vmSize"),
                    field(row, "powerState").casefold().removeprefix("vm "))


def azure_disk(row):
    return Resource(field(row, "id"), field(row["sku"], "name"),
                    field(row, "diskState").casefold())


AZURE_COMMANDS = {
    "vms": ["az", "vm", "list", "--show-details", "--output", "json"],
    "disks": ["az", "disk", "list", "--output", "json"],
}


def azure_read_only(kind, command):
    base = AZURE_COMMANDS[kind]
    if command[:len(base)] != base:
        return False
    options = command[len(base):]
    if len(options) % 2:
        return False
    return all(key in ("--subscription", "--resource-group")
               and re.fullmatch(r"[\w.() +@-]+", value) and not value.startswith("-")
               for key, value in zip(options[::2], options[1::2]))


# プロバイダーごとに読み取り専用コマンドの検証と出力変換を登録する。
PROVIDERS = {"azure": (azure_read_only, {"vms": azure_vm, "disks": azure_disk})}


def load_config(path):
    config = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    if not isinstance(config, dict):
        raise ValueError("設定は JSON オブジェクトで指定してください")
    provider = config.setdefault("provider", "azure")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("provider は登録済みのプロバイダーを指定してください")
    validator, _ = PROVIDERS[provider]
    commands = config.setdefault("commands", AZURE_COMMANDS if provider == "azure" else None)
    if not isinstance(commands, dict) or commands.keys() != {"vms", "disks"}:
        raise ValueError("commands に vms と disks を指定してください")
    for kind, command in commands.items():
        if (not isinstance(command, list) or not command
                or not all(isinstance(arg, str) and arg for arg in command)
                or not validator(kind, command)):
            raise ValueError(f"commands.{kind} は許可された読み取り専用コマンドを指定してください")
    if type(config.setdefault("dry_run", True)) is not bool:
        raise ValueError("dry_run は true または false を指定してください")
    timeout = config.setdefault("timeout_seconds", 60)
    if type(timeout) not in (int, Decimal) or not 0 < timeout <= 3600 or float(timeout) == 0:
        raise ValueError("timeout_seconds は 0 より大きく 3600 以下を指定してください")
    config["timeout_seconds"] = float(timeout)
    currency = config.setdefault("currency", "JPY")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency は英大文字 3 文字で指定してください")
    sizes = config.setdefault("oversized_sizes", {})
    if not isinstance(sizes, dict) or any(not isinstance(target, str) or not target.strip()
                                          or size == target for size, target in sizes.items()):
        raise ValueError("oversized_sizes は現行サイズから縮小先サイズへの対応表を指定してください")
    for key in ("vm_monthly_prices", "disk_monthly_prices"):
        prices = config.setdefault(key, {})
        if not isinstance(prices, dict):
            raise ValueError(f"{key} は月額単価表を指定してください")
        for sku, value in prices.items():
            if type(value) not in (int, Decimal) or not Decimal(value).is_finite() or value < 0:
                raise ValueError(f"{key}.{sku} は 0 以上の有限な月額を指定してください")
            prices[sku] = Decimal(value)
    return config


def read_resources(command, parse, timeout):
    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8",
                                stdin=subprocess.DEVNULL, timeout=timeout, check=False)
    except FileNotFoundError:
        raise InventoryError("CLI が見つかりません") from None
    except subprocess.TimeoutExpired:
        raise InventoryError("CLI がタイムアウトしました") from None
    except (OSError, UnicodeError):
        raise InventoryError("CLI を実行できないか、出力を読み取れません") from None
    if result.returncode:
        if re.search(r"az login|authentication|unauthorized|invalidauthenticationtoken|"
                     r"authorizationfailed|forbidden|aadsts", result.stderr, re.IGNORECASE):
            raise InventoryError(f"CLI の認証または権限エラー（終了コード {result.returncode}）")
        raise InventoryError(f"CLI コマンド失敗（終了コード {result.returncode}）")
    try:
        rows = json.loads(result.stdout)
        if not isinstance(rows, list):
            raise ValueError("配列が必要です")
        resources = [parse(row) for row in rows]
        if len({resource.identity for resource in resources}) != len(resources):
            raise ValueError("リソース ID の重複")
        return resources
    except (ValueError, TypeError, KeyError):
        raise InventoryError("CLI の JSON 出力を解釈できません") from None


def inventory(config):
    _, parsers = PROVIDERS[config["provider"]]
    resources = {}
    for kind, parse in parsers.items():
        try:
            resources[kind] = read_resources(config["commands"][kind], parse, config["timeout_seconds"])
        except InventoryError as error:
            raise InventoryError(f"{kind}: {error}") from None

    vm_prices = config["vm_monthly_prices"]
    sizes = config["oversized_sizes"]
    candidates = []
    for vm in resources["vms"]:
        target = sizes.get(vm.sku)
        price = vm_prices.get(vm.sku)
        if vm.state in ("stopped", "deallocated"):
            reason = f"停止中 VM（{vm.state}、削除候補）"
            if target is not None:
                reason += f"／過大 SKU（縮小候補 {target}）"
            candidates.append(Candidate(vm, reason, price))
        elif target is not None:
            target_price = vm_prices.get(target)
            savings = (max(Decimal(0), price - target_price)
                       if price is not None and target_price is not None else None)
            candidates.append(Candidate(vm, f"過大 SKU（{vm.sku} → {target}、縮小候補）", savings))
    for disk in resources["disks"]:
        if disk.state == "unattached":
            candidates.append(Candidate(disk, "未接続ディスク（削除候補）",
                                        config["disk_monthly_prices"].get(disk.identity)))
    return sorted(candidates, key=lambda candidate: candidate.resource.identity)


def marker_prefix(day):
    return f"<!-- cloud-waste-inventory {day} "


def markdown_text(value):
    # 区切りや GitLab のメンションとして解釈される文字をエスケープする。
    return re.sub(r"[&<>`*_|@\\]", lambda match: f"&#{ord(match[0])};", " ".join(value.split()))


def markdown(candidates, day, currency):
    lines = [f"{marker_prefix(day)}{len(candidates)}件 -->",
             f"## クラウドリソースの無駄棚卸し（{day} UTC、{len(candidates)}件）", ""]
    known = [candidate.savings for candidate in candidates if candidate.savings is not None]
    for candidate in candidates:
        resource = candidate.resource
        amount = (f"{currency} {candidate.savings:,.2f}/月"
                  if candidate.savings is not None else "見積り不可")
        lines.append(f"- {markdown_text(candidate.reason)} · {markdown_text(resource.identity)} "
                     f"（{markdown_text(resource.sku)}） · {amount}")
    if not candidates:
        lines.append("削減候補はありません。")
    total = f"{currency} {sum(known):,.2f}/月" if known or not candidates else "見積り不可"
    lines.extend(["", f"**削除・縮小した場合の月額削減見込み合計（見積り可能分）** {total}",
                  f"見積り不可 {len(candidates) - len(known)}件"])
    return "\n".join(lines)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def hub_notes_url(issue_url):
    parts = urlsplit(issue_url)
    match = re.fullmatch(r"/(.+)/-/issues/([0-9]+)/?", parts.path)
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.query or parts.fragment or match is None):
        raise ValueError("GITLAB_HUB_ISSUE_URL に HTTPS の GitLab issue URL を指定してください")
    project = quote(unquote(match[1]), safe="")
    path = f"/api/v4/projects/{project}/issues/{match[2]}/notes"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def post(report, day, timeout):
    notes_url = hub_notes_url(os.environ.get("GITLAB_HUB_ISSUE_URL", ""))
    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        raise ValueError("GITLAB_TOKEN を設定してください")
    opener = build_opener(NoRedirect())
    headers = {"PRIVATE-TOKEN": token, "Content-Type": "application/json"}
    lock_path = Path.home() / ".config/cloud-waste-inventory/post.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for page in range(1, 101):
            query = urlencode({"per_page": 100, "page": page})
            request = Request(f"{notes_url}?{query}", headers=headers)
            with opener.open(request, timeout=timeout) as response:
                notes = json.load(response)
                next_page = response.headers.get("X-Next-Page")
            if not isinstance(notes, list) or any(
                not isinstance(note, dict) or not isinstance(note.get("body"), str) for note in notes
            ):
                raise ValueError("GitLab のコメント一覧を解釈できません")
            if any(re.fullmatch(re.escape(marker_prefix(day)) + r"[0-9]+件 -->",
                                note["body"].partition("\n")[0]) for note in notes):
                return "同日の棚卸しが投稿済みです"
            if not next_page and len(notes) < 100:
                break
        else:
            raise ValueError("GitLab のコメント一覧の取得上限に達しました")

        payload = json.dumps({"body": report}, ensure_ascii=False).encode("utf-8")
        with opener.open(Request(notes_url, data=payload, headers=headers), timeout=timeout) as response:
            if response.status != 201:
                raise ValueError(f"GitLab の投稿結果を確認できません（HTTP {response.status}）")
    return "GitLab ハブ issue に投稿しました"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        day = datetime.now(timezone.utc).date()
        report = markdown(inventory(config), day, config["currency"])
        print(report)
        status = ("dry-run のため投稿しません" if config["dry_run"]
                  else post(report, day, config["timeout_seconds"]))
        print(status, file=sys.stderr)
    except InventoryError as error:
        print(f"未確認（{error}）")
        return 3
    except (OSError, ValueError, OverflowError, DecimalException) as error:
        print(f"cloud-waste-inventory: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
