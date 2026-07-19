"""新境界テスト 1 本ずつの被覆行列。

各変異について「どのテストメソッドが落ちたか」を記録し、最後に **一度も落ちなかった新テスト**
を列挙する。そこに名前が出るテストは、境界のどの性質も掴んでいない＝空振りの疑いがある。
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/tmp/t6-scratch")
from mutate import MUTANTS, ROOT, PKG          # noqa: E402

NEW_CLASSES = ("TestHookResolution", "TestDoctorWiringFindings")
NEW_INTAKE = ("test_parse_intake_records_normalizes_title_and_id",
              "test_parse_intake_records_passes_unknown_fields_through",
              "test_run_intake_accepts_non_string_id",
              "test_run_intake_blank_id_falls_back_to_generated_id",
              "test_run_intake_dedups_non_string_id")

SRC = (ROOT / "tests/test_agent_project.py").read_text(encoding="utf-8")


def methods_of(cls):
    body = SRC.split("class %s(" % cls, 1)[1]
    body = re.split(r"\nclass ", body, 1)[0]
    return ["%s.%s" % (cls, m) for m in re.findall(r"\n    def (test_\w+)", body)]


NEW_TESTS = []
for c in NEW_CLASSES:
    NEW_TESTS += methods_of(c)
NEW_TESTS += ["TestIntake." + m for m in NEW_INTAKE]


def run_all():
    """新テスト全部を1回で走らせ、落ちた（FAIL/ERROR）テスト名の集合を返す。"""
    p = subprocess.run([sys.executable, "tests/test_agent_project.py", "-v"] + NEW_TESTS,
                       cwd=ROOT, capture_output=True, text=True,
                       env={"PYTHONPATH": ".", "PATH": "/usr/bin:/bin"})
    bad = set()
    for line in p.stderr.splitlines():
        m = re.match(r"(?:FAIL|ERROR): (\w+) \(__main__\.(\w+)\)", line)
        if m:
            bad.add("%s.%s" % (m.group(2), m.group(1)))
    return p.returncode, bad


rc, bad = run_all()
print("baseline: rc=%d 落ちたテスト=%s（新テスト総数 %d）" % (rc, sorted(bad) or "なし", len(NEW_TESTS)))
assert rc == 0 and not bad, "baseline が緑でない"

killed_by = {t: [] for t in NEW_TESTS}
for mid, fname, what, old, new, _tests in MUTANTS:
    path = PKG / fname
    src = path.read_text(encoding="utf-8")
    assert src.count(old) == 1, (mid, "アンカー不一致")
    path.write_text(src.replace(old, new), encoding="utf-8")
    try:
        _rc, bad = run_all()
    finally:
        path.write_text(src, encoding="utf-8")
    unknown = bad - set(NEW_TESTS)
    for t in bad & set(NEW_TESTS):
        killed_by[t].append(mid)
    print("%-4s %-2s %d本が赤 %s%s" % (mid, fname[:2], len(bad), sorted(bad & set(NEW_TESTS))[:3],
                                       " +他" if len(bad) > 3 else ""))
    assert not unknown, (mid, unknown)

print("\n=== 被覆行列（テスト -> それを殺した変異）===")
for t in NEW_TESTS:
    print("  %-58s %s" % (t, ",".join(killed_by[t]) or "*** どの変異でも落ちない ***"))

silent = [t for t in NEW_TESTS if not killed_by[t]]
print("\n空振り候補: %s" % (silent or "なし（新テスト %d 本すべてが最低1つの変異を検出）" % len(NEW_TESTS)))
