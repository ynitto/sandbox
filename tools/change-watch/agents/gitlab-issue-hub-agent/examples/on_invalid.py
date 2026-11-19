"""書式が違う依頼を差し戻す例。"""
import sys

from hub_reply import head, load

EXAMPLES = {
    "report": "【依頼】[report] 売上の集計\n対象: 東日本\n期間: 2026-10",
    "inquiry": "【依頼】[inquiry] 問い合わせへの回答案\n差出人: 山田\n受信日時: 2026-11-19 09:30\n\n納期を教えてください。",
    "account": "【依頼】[account] 権限の追加\n対象者: 山田\n操作: 追加\n期日: 2026-12-01",
}


if __name__ == "__main__":
    check, change = load(sys.argv)
    print(head("【差し戻し】", check, change))
    print()
    for violation in check["violations"]:
        print(f"- {violation['message']}")
    example = EXAMPLES.get(check["kind"])
    if example:
        print(f"\n書き方の見本\n\n```\n{example}\n```")
