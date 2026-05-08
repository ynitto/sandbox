import sys
from pathlib import Path

# scripts/ is a sibling of tests/ inside the skill directory
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
