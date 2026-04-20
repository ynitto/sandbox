"""wt-manager の依存パッケージをインストールする。"""

import subprocess
import sys


def main() -> None:
    packages = ["pywin32", "psutil"]
    subprocess.check_call([sys.executable, "-m", "pip", "install", *packages])
    print("インストール完了:", ", ".join(packages))


if __name__ == "__main__":
    main()
