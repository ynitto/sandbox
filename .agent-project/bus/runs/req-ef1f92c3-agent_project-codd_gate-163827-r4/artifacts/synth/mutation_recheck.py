# synth: gate が「生存」と報告した 4 ミューテーション（fail-2 と minor 2 件）＋
# 統合で入れ替えた型不正経路が、追加したテストで赤くなるかを再確認する。
# 作業ツリーは触らず複製ツリー上で行う。
import shutil, subprocess, sys, tempfile
from pathlib import Path

SRC = Path("/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/agent-flow-ws-76445-yirzzhrm/sandbox/tools/agent-project")

MUTATIONS = [
    ("gate fail-2: `id: 0` を潰す素直な書き方へ退行", "agent_project/model.py",
     '        rid = raw.get("id")\n'
     '        if rid not in (None, ""):            # `0` は「id が無い」ではないので or "" で潰さない\n'
     '            rid = str(rid).strip()\n'
     '            if rid:\n'
     '                spec["id"] = rid\n',
     '        rid = str(raw.get("id", "") or "").strip()\n'
     '        if rid:\n'
     '            spec["id"] = rid\n',
     "TestIntake"),
    ("gate minor-2a: sorted 撤去（走査順が非決定に）", "agent_project/hooks.py",
     '    for path in sorted(sib.glob("*.py")):', '    for path in sib.glob("*.py"):',
     "TestHookResolution"),
    ("gate minor-2b: _ 始まり/非識別子の除外を撤去", "agent_project/hooks.py",
     '        if name.startswith("_") or not name.isidentifier():\n'
     '            continue                      # `agent-project.py` のような非 module 名はここで落ちる\n',
     "", "TestHookResolution"),
    ("gate minor-1: hooks 型不正の warn を出さない", "agent_project/doctor.py",
     '    if getattr(cfg, "hooks_error", None):\n        return warn("hooks の設定が読めない", cfg.hooks_error)\n',
     "", "TestDoctorWiringFindings"),
    ("統合分: 型不正を記録せず黙って捨てる（configfile 側）", "agent_project/configfile.py",
     '        hooks_error=_hooks_config_error(getattr(args, "hooks", None)),\n', "",
     "TestHookConfig TestDoctorWiringFindings"),
    ("統合分: 非文字列の値だけ落として理由を残さない", "agent_project/configfile.py",
     '    kept = _normalize_hooks(raw)\n'
     '    dropped = sorted(str(k) for k in raw if str(k).strip() not in kept)\n'
     '    if dropped:\n'
     '        return "hooks の値が module 名（文字列）になっていないキー: " + " / ".join(dropped)\n',
     "", "TestHookConfig"),
]


def run(tree: Path, tests: str):
    p = subprocess.run([sys.executable, "tests/test_agent_project.py"] + tests.split(),
                       cwd=tree, capture_output=True, text=True, env={"PYTHONPATH": ".", "PATH": "/usr/bin:/bin"})
    return p.returncode, (p.stderr or "").strip().splitlines()[-1]


def main():
    base = Path(tempfile.mkdtemp(prefix="synth-mut-"))
    tree = base / "agent-project"
    shutil.copytree(SRC, tree)
    rc, tail = run(tree, "TestIntake TestHookResolution TestHookConfig TestDoctorWiringFindings")
    print(f"baseline: rc={rc} {tail}")
    if rc != 0:
        print("ベースラインが緑でない。ミューテーション結果は読めない。"); return 1
    bad = 0
    for desc, rel, before, after, tests in MUTATIONS:
        path = tree / rel
        original = path.read_text(encoding="utf-8")
        if before not in original:
            print(f"SKIP  {desc}: 対象コードが見つからない"); bad += 1; continue
        try:
            path.write_text(original.replace(before, after, 1), encoding="utf-8")
            rc, tail = run(tree, tests)
        finally:
            path.write_text(original, encoding="utf-8")
            assert path.read_text(encoding="utf-8") == original
        verdict = "KILLED  " if rc != 0 else "SURVIVED"
        if rc == 0:
            bad += 1
        print(f"{verdict} {desc} -> {tail}")
    print("すべて KILLED" if bad == 0 else f"{bad} 件が生存/未適用")
    shutil.rmtree(base, ignore_errors=True)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
