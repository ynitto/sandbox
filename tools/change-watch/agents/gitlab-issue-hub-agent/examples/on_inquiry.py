"""問い合わせへの回答案の依頼を受け付ける例。"""
import sys

from hub_reply import free_text, head, load


if __name__ == "__main__":
    check, change = load(sys.argv)
    lines = free_text(check, change).split("\n")
    print(head("【返信】", check, change))
    print(f"\n{check['fields']['差出人']} さんからの問い合わせを受け付けました（{len(lines)} 行）。")
