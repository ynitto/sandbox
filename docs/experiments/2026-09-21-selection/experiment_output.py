"""Keep reruns separate from the archived observations beside these scripts."""
from pathlib import Path
from tempfile import mkdtemp

_RUN_DIR = None

def output_path(name):
    global _RUN_DIR
    if _RUN_DIR is None:
        parent = Path(__file__).resolve().parent / "reruns"
        parent.mkdir(exist_ok=True)
        _RUN_DIR = Path(mkdtemp(prefix="run-", dir=parent))
        print(f"Evaluation output: {_RUN_DIR}", flush=True)
    return _RUN_DIR / name
