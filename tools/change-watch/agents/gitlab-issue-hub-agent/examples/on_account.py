"""アカウント・権限の申請を受け付ける例。"""
import sys

from hub_reply import head, load


if __name__ == "__main__":
    check, change = load(sys.argv)
    fields = check["fields"]
    print(head("【返信】", check, change))
    print(f"\nアカウント・権限の申請を受け付けました（対象者: {fields['対象者']}、操作: {fields['操作']}、期日: {fields['期日']}）。")
