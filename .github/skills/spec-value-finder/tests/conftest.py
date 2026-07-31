import sys
from pathlib import Path

# scripts/ は tests/ の兄弟ディレクトリ
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
