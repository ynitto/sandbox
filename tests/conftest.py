import sys
from pathlib import Path

# pipeline package moved to .github/skills/graph-pipeline/scripts/
sys.path.insert(0, str(Path(__file__).parent.parent / ".github/skills/graph-pipeline/scripts"))
