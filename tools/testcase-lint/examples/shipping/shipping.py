import sys


def shipping(total: int) -> int:
    return 0 if total >= 5000 else 500


if __name__ == "__main__":
    print(shipping(int(sys.argv[1])))
