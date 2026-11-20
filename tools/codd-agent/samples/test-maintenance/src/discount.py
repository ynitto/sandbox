"""商品合計から整数円の割引額を計算する。"""
import argparse


def discount(subtotal: int) -> int:
    return subtotal // 10 if subtotal >= 10000 else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="割引後の金額を円で表示する")
    parser.add_argument("subtotal", type=int, help="商品合計（円）")
    subtotal = parser.parse_args().subtotal
    print(subtotal - discount(subtotal))
