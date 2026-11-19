"""集計・報告の依頼を受け付ける例。"""
import sys

from hub_reply import head, load


if __name__ == "__main__":
    check, change = load(sys.argv)
    fields = check["fields"]
    print(head("【返信】", check, change))
    print(f"\n集計・報告の依頼を受け付けました（対象: {fields['対象']}、期間: {fields['期間']}）。")
