# 検証(2): テストが空振りしていないか。フック実装を1つずつ故意に壊し、
# 該当テストが赤くなるかを見る。作業ツリーは触らず、複製ツリー上で行う。
import shutil, subprocess, sys, tempfile, os
from pathlib import Path

SRC = Path("/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/agent-flow-ws-76445-yirzzhrm/sandbox/tools/agent-project")

# (説明, 対象ファイル, 置換前, 置換後, 走らせるテスト)
MUTATIONS = [
    ("明示指定の解決失敗で自動検出へ落ちる（意図の黙殺）", "agent_project/hooks.py",
     "        mod = _hook_import(name, required)\n        _HOOK_CACHE[capability] = mod\n        return mod\n",
     "        mod = _hook_import(name, required)\n        if mod is not None:\n            _HOOK_CACHE[capability] = mod\n            return mod\n",
     "TestHookResolution"),
    ("キャッシュしない（毎回スキャン）", "agent_project/hooks.py",
     "    if capability in _HOOK_CACHE:\n        return _HOOK_CACHE[capability]\n", "",
     "TestHookResolution"),
    ("前置フィルタ撤去（無関係 sibling を総当たり import）", "agent_project/hooks.py",
     "        if not all(p.search(src) for p in pats):\n            continue\n", "",
     "TestHookResolution"),
    ("必須属性チェック撤去（契約不足 module を採用）", "agent_project/hooks.py",
     "    return mod if all(hasattr(mod, a) for a in required) else None   # 上記 (a)/(c)",
     "    return mod",
     "TestHookResolution"),
    ("前半キー（hooks.wiring）を引かない", "agent_project/hooks.py",
     "    for key in (capability, capability.split(\".\", 1)[0]):",
     "    for key in (capability,):",
     "TestHookResolution"),
    ("sorted 撤去（走査順が非決定に）", "agent_project/hooks.py",
     "    for path in sorted(sib.glob(\"*.py\")):", "    for path in sib.glob(\"*.py\"):",
     "TestHookResolution"),
    ("_ 始まり/非識別子の除外を撤去", "agent_project/hooks.py",
     "        if name.startswith(\"_\") or not name.isidentifier():\n            continue                      # `agent-project.py` のような非 module 名はここで落ちる\n",
     "", "TestHookResolution"),
    ("片方だけ解決でもプロバイダを呼ぶ（or -> and）", "agent_project/doctor.py",
     "    if detect is None or render is None:", "    if detect is None and render is None:",
     "TestDoctorWiringFindings"),
    ("プロバイダ例外を畳まない", "agent_project/doctor.py",
     "    except Exception:\n        return out", "    except ZeroDivisionError:\n        return out",
     "TestDoctorWiringFindings"),
    ("設定ミス warn を出さない", "agent_project/doctor.py",
     "    for capability in (\"wiring.detect\", \"wiring.findings\"):\n        reason = _hook_resolution_error(capability, cfg)\n        if reason:",
     "    for capability in ():\n        reason = _hook_resolution_error(capability, cfg)\n        if reason:",
     "TestDoctorWiringFindings"),
    ("hooks 型不正の warn を出さない", "agent_project/doctor.py",
     "    if hooks is not None and not isinstance(hooks, dict):",
     "    if False:",
     "TestDoctorWiringFindings"),
    ("id 正規化: 0 を潰す素直な書き方へ退行", "agent_project/model.py",
     "        rid = raw.get(\"id\")\n        if rid not in (None, \"\"):            # `0` は「id が無い」ではないので or \"\" で潰さない\n            rid = str(rid).strip()\n            if rid:\n                spec[\"id\"] = rid\n",
     "        rid = str(raw.get(\"id\", \"\") or \"\").strip()\n        if rid:\n            spec[\"id\"] = rid\n",
     "TestIntake"),
    ("id を strip しない（空白付き id がそのまま）", "agent_project/model.py",
     "            rid = str(rid).strip()\n", "            rid = str(rid)\n",
     "TestIntake"),
    ("id を文字列化しない（非文字列 id で AttributeError）", "agent_project/model.py",
     "            rid = str(rid).strip()\n", "            rid = rid.strip() if hasattr(rid, 'strip') else rid\n",
     "TestIntake"),
    ("title を strip/文字列化しない", "agent_project/model.py",
     "        title = str(raw.get(\"title\", \"\") or \"\").strip()",
     "        title = raw.get(\"title\", \"\")",
     "TestIntake"),
    ("自動配線を復活させる（cfg を黙って補う）", "agent_project/configfile.py",
     "        update_installer=str(getattr(args, \"update_installer\", \"install.sh\") or \"install.sh\"),\n    )\n    return cfg",
     "        update_installer=str(getattr(args, \"update_installer\", \"install.sh\") or \"install.sh\"),\n    )\n    cfg.regression_cmd = cfg.regression_cmd or \"codd-gate verify\"\n    return cfg",
     "TestCoddGateNoAutoWiring TestLoopEngineering"),
]

def run(tree: Path, tests: str):
    p = subprocess.run([sys.executable, "tests/test_agent_project.py"] + tests.split(),
                       cwd=tree, env={**os.environ, "PYTHONPATH": "."},
                       capture_output=True, text=True, timeout=600)
    return p.returncode, (p.stdout + p.stderr)

base = Path(tempfile.mkdtemp()) / "base"
shutil.copytree(SRC, base)
print("== ベースライン（無改変）==")
for t in ["TestHookResolution", "TestDoctorWiringFindings", "TestIntake",
          "TestCoddGateNoAutoWiring TestLoopEngineering"]:
    rc, o = run(base, t)
    print(f"  {t}: rc={rc} {'OK' if rc == 0 else 'FAIL'}")

print("\n== ミューテーション ==")
results = []
for i, (desc, rel, before, after, tests) in enumerate(MUTATIONS, 1):
    tree = Path(tempfile.mkdtemp()) / "m"
    shutil.copytree(SRC, tree)
    f = tree / rel
    src = f.read_text(encoding="utf-8")
    if before not in src:
        print(f"{i:2}. [適用不可] {desc} -- 置換前文字列が見つからない ({rel})")
        results.append((i, desc, "NOT-APPLIED"))
        shutil.rmtree(tree.parent, ignore_errors=True)
        continue
    f.write_text(src.replace(before, after, 1), encoding="utf-8")
    rc, out = run(tree, tests)
    killed = rc != 0
    tail = [l for l in out.splitlines() if l.startswith(("FAIL:", "ERROR:"))][:3]
    print(f"{i:2}. [{'KILLED' if killed else '*** SURVIVED ***'}] {desc}")
    for l in tail:
        print(f"      {l}")
    results.append((i, desc, "KILLED" if killed else "SURVIVED"))
    shutil.rmtree(tree.parent, ignore_errors=True)

print("\n== 集計 ==")
k = sum(1 for _, _, r in results if r == "KILLED")
s = [x for x in results if x[2] == "SURVIVED"]
n = [x for x in results if x[2] == "NOT-APPLIED"]
print(f"KILLED {k} / {len(results)}   SURVIVED {len(s)}   NOT-APPLIED {len(n)}")
for i, d, _ in s:
    print(f"  SURVIVED: {i}. {d}")
for i, d, _ in n:
    print(f"  NOT-APPLIED: {i}. {d}")
