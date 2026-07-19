"""故意破壊（mutation）で新境界テストが空振りでないことを実測する。

各変異はフック境界の1性質だけを壊し、その性質を主張するテストが実際に赤くなるかを見る。
「名前を変えただけで何も検証していない」テストは、どの変異でも緑のままになる。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path("/tmp/t6-scratch/agent-project")
PKG = ROOT / "agent_project"

MUTANTS = [
    # (ID, 対象ファイル, 壊す性質, old, new, 落ちるはずのテスト)
    ("M1", "hooks.py", "フック解決そのもの（常に None）",
     "    if capability in _HOOK_CACHE:",
     "    if True:\n        return None\n    if capability in _HOOK_CACHE:",
     "TestHookResolution"),
    ("M2", "hooks.py", "前置フィルタ（無関係 sibling を総当たり import）",
     "        if not all(p.search(src) for p in pats):\n            continue",
     "        if False:\n            continue",
     "TestHookResolution.test_unrelated_siblings_are_not_imported"),
    ("M3", "hooks.py", "明示指定の失敗で自動検出へ落ちない",
     "        mod = _hook_import(name, required)\n        _HOOK_CACHE[capability] = mod\n        return mod",
     "        mod = _hook_import(name, required) or _hook_scan_siblings(required)\n"
     "        _HOOK_CACHE[capability] = mod\n        return mod",
     "TestHookResolution.test_explicit_name_does_not_fall_back_to_scan"),
    ("M4", "hooks.py", "解決結果のキャッシュ",
     "    if capability in _HOOK_CACHE:\n        return _HOOK_CACHE[capability]",
     "    if False:\n        return _HOOK_CACHE[capability]",
     "TestHookResolution"),
    ("M5", "hooks.py", "必須属性の検査（契約不足の module を採用）",
     "    return mod if all(hasattr(mod, a) for a in required) else None   # 上記 (a)/(c)",
     "    return mod",
     "TestHookResolution"),
    ("M6", "hooks.py", "能力キー名（wiring.detect -> detect へ改名）",
     '    "wiring.detect":   ("detect_wiring",),',
     '    "detect":   ("detect_wiring",),',
     "TestHookResolution"),
    ("M7", "hooks.py", "sibling 不在時に sys.path を汚さない",
     "    sib = _hook_sibling_dir() if sib is None else sib\n"
     "    if not required or not sib.is_dir():\n        return None",
     "    sib = _hook_sibling_dir() if sib is None else sib\n"
     "    _hook_ensure_path(sib)\n"
     "    if not required or not sib.is_dir():\n        return None",
     "TestHookResolution.test_missing_sibling_dir_resolves_to_none"),
    ("M8", "doctor.py", "片側だけ解決したときプロバイダを呼ばない",
     "    if detect is None or render is None:",
     "    if detect is None and render is None:",
     "TestDoctorWiringFindings.test_half_resolved_provider_does_not_run"),
    ("M9", "doctor.py", "プロバイダ例外の畳み込み",
     "    except Exception:\n        return []",
     "    except ZeroDivisionError:\n        return []",
     "TestDoctorWiringFindings"),
    ("M10", "doctor.py", "明示指定ミスの warn（黙って空へ）",
     "        return _hook_misconfig_findings(cfg)",
     "        return []",
     "TestDoctorWiringFindings.test_unresolvable_explicit_provider_warns"),
    ("M11", "doctor.py", "注入引数の受け渡し（which を握り潰す）",
     "            repos_path=repo_registry_path(cfg), which=which, run=run)",
     "            repos_path=repo_registry_path(cfg), which=shutil.which, run=run)",
     "TestDoctorWiringFindings.test_injected_arguments_reach_provider"),
    ("M12", "model.py", "id の型正規化（生の値を素通し）",
     "        rid = raw.get(\"id\")\n"
     "        if rid not in (None, \"\"):            # `0` は「id が無い」ではないので or \"\" で潰さない\n"
     "            rid = str(rid).strip()\n"
     "            if rid:\n"
     "                spec[\"id\"] = rid",
     "        rid = raw.get(\"id\")\n"
     "        if rid not in (None, \"\"):\n            spec[\"id\"] = rid",
     "TestIntake"),
    ("M13", "model.py", "title の正規化（strip しない）",
     '        spec = {"title": title}',
     '        spec = {"title": raw.get("title")}',
     "TestIntake"),
    # 以下 4 本は「境界が緩む」方向の変異。解決を殺す変異（M1 等）では守っている性質が
    # たまたま同じ返り値になって隠れるため、緩める方向でしか観測できない。
    ("M14", "hooks.py", "プロバイダ名を本体へ書き戻す（走査が空振りしたら固有名で import）",
     "        mod = _hook_import(name, required)\n        if mod is not None:\n            return mod\n    return None",
     "        mod = _hook_import(name, required)\n        if mod is not None:\n            return mod\n"
     "    return _hook_import(\"codd_gate_wiring\", required)",
     "TestHookResolution.test_empty_sibling_dir_resolves_to_none"),
    ("M15", "doctor.py", "プロバイダの findings を本体が解釈する（不透明性）",
     "        return render.doctor_findings(judgment)",
     "        return [f for f in render.doctor_findings(judgment) if f.get(\"severity\") != \"warn\"]",
     "TestDoctorWiringFindings.test_provider_findings_pass_through"),
    ("M16", "doctor.py", "未指定の不在も所見にする（無言縮退）",
     "        err = _hook_resolution_error(capability, cfg)\n        if not err:\n            continue",
     "        err = _hook_resolution_error(capability, cfg) or \"配線プロバイダが見つからない\"",
     "TestDoctorWiringFindings.test_no_provider_degrades_to_empty"),
    ("M17", "model.py", "未知フィールドの素通し（本体が検出器の語彙を持つ）",
     '        spec.update({k: v for k, v in raw.items() if k not in ("title", "id")})',
     '        spec.update({k: v for k, v in raw.items() if k in ("verify", "note")})',
     "TestIntake.test_parse_intake_records_passes_unknown_fields_through"),
]


def run(tests):
    p = subprocess.run([sys.executable, "tests/test_agent_project.py"] + tests.split(),
                       cwd=ROOT, capture_output=True, text=True,
                       env={"PYTHONPATH": ".", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})
    tail = [l for l in p.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:", "OK", "FAILED", "Ran "))]
    return p.returncode, tail


print("=== baseline（変異なし）===")
for t in ("TestHookResolution", "TestDoctorWiringFindings", "TestIntake"):
    print(f"  {t}: rc={run(t)[0]} {run(t)[1]}")

print("\n=== mutants ===")
rows = []
for mid, fname, what, old, new, tests in MUTANTS:
    path = PKG / fname
    src = path.read_text(encoding="utf-8")
    if src.count(old) != 1:
        rows.append((mid, what, tests, "SKIP", f"アンカー不一致 count={src.count(old)}"))
        continue
    path.write_text(src.replace(old, new), encoding="utf-8")
    try:
        rc, tail = run(tests)
    finally:
        path.write_text(src, encoding="utf-8")
    verdict = "検出" if rc != 0 else "空振り(!!)"
    detail = "; ".join(x for x in tail if x.startswith(("FAIL:", "ERROR:")))[:200] or "; ".join(tail)
    rows.append((mid, what, tests, verdict, detail))
    print(f"  {mid} {verdict:9} {what}")
    print(f"       -> {detail}")

print("\n=== 空振り一覧 ===")
bad = [r for r in rows if r[3] != "検出"]
print("なし（全変異を検出）" if not bad else "\n".join(str(r) for r in bad))
