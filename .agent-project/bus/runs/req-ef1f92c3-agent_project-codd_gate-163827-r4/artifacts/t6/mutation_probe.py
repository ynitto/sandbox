#!/usr/bin/env python3
"""新テストが「空振り」でないことを実測する。

各ミューテーションは差し込み点の 1 つの性質だけを壊し、対応するテストが赤になることを確かめる。
テストが名前を変えただけで何も検証していないなら、壊しても緑のまま通る。

  python3 mutation_probe.py <repo>/tools/agent-project
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
PKG = ROOT / "agent_project"

# (ID, 何を壊すか, ファイル, 置換前, 置換後, 赤になるべきテスト)
MUTATIONS = [
    ("M1", "sibling 走査の前置フィルタを外す（無関係 sibling も import する）", PKG / "hooks.py",
     "        if not all(p.search(src) for p in pats):\n            continue\n",
     "        if False:\n            continue\n",
     ["TestHookResolution.test_prefilter_does_not_import_unrelated_siblings"]),

    ("M2", "明示指定の解決失敗で sibling 自動検出へ落ちる", PKG / "hooks.py",
     "        mod = _hook_import(name, required)\n"
     "        _HOOK_CACHE[capability] = mod\n"
     "        return mod\n",
     "        mod = _hook_import(name, required)\n"
     "        if mod is not None:\n"
     "            _HOOK_CACHE[capability] = mod\n"
     "            return mod\n",
     ["TestHookResolution.test_configured_name_that_fails_does_not_fall_back",
      "TestDoctorWiringFindings.test_configured_provider_that_fails_is_reported"]),

    ("M3", "必須属性の検査を外す（契約不足の module を採用する）", PKG / "hooks.py",
     "    return mod if all(hasattr(mod, a) for a in required) else None",
     "    return mod",
     ["TestHookResolution.test_configured_module_without_contract_is_rejected"]),

    ("M4", "解決結果をキャッシュしない", PKG / "hooks.py",
     "    if capability in _HOOK_CACHE:\n        return _HOOK_CACHE[capability]\n",
     "    if False:\n        return _HOOK_CACHE[capability]\n",
     ["TestHookResolution.test_result_is_cached_including_misses"]),

    ("M5", "設定の明示指定を読まない（常に自動検出）", PKG / "hooks.py",
     '    hooks = getattr(cfg, "hooks", None) if cfg is not None else None\n'
     "    if not isinstance(hooks, dict):\n        return None\n",
     "    return None\n    hooks = None\n",
     ["TestHookResolution.test_configured_name_wins_over_sibling_scan"]),

    ("M6", "sibling 走査が常に不発", PKG / "hooks.py",
     "    sib = _hook_sibling_dir() if sib is None else sib\n"
     "    if not required or not sib.is_dir():\n        return None\n",
     "    return None\n    sib = sib\n",
     ["TestHookResolution.test_sibling_scan_resolves_provider_by_capability"]),

    ("M7", "片方の能力だけでプロバイダ経路へ入る", PKG / "doctor.py",
     "    if detect is None or render is None:",
     "    if detect is None and render is None:",
     ["TestDoctorWiringFindings.test_half_resolved_capability_does_not_call_provider"]),

    ("M8", "プロバイダ由来の例外を畳まない", PKG / "doctor.py",
     "    except Exception:\n        return out\n",
     "    except ZeroDivisionError:\n        return out\n",
     ["TestDoctorWiringFindings.test_provider_exception_does_not_break_doctor"]),

    ("M9", "注入引数をプロバイダへ渡さない", PKG / "doctor.py",
     "            regression_cmd=cfg.regression_cmd, intake_cmd=cfg.intake_cmd,\n"
     "            repos_path=repo_registry_path(cfg), which=which, run=run)",
     "            regression_cmd=None, intake_cmd=None,\n"
     "            repos_path=None, which=None, run=None)",
     ["TestDoctorWiringFindings.test_injected_arguments_reach_the_provider"]),

    ("M10", "設定ミスを所見にしない（既定の不在と同じ無言扱い）", PKG / "doctor.py",
     'def _hook_misconfig_findings(cfg: "Config") -> "list[dict]":\n',
     'def _hook_misconfig_findings(cfg: "Config") -> "list[dict]":\n    return []\n',
     ["TestDoctorWiringFindings.test_configured_provider_that_fails_is_reported",
      "TestDoctorWiringFindings.test_broken_hooks_type_is_reported"]),

    ("M11", "intake の id を型正規化しない（r0 で落ちた振る舞い）", PKG / "model.py",
     "            rid = str(rid).strip()\n            if rid:\n                spec[\"id\"] = rid\n",
     "            spec[\"id\"] = rid\n",
     ["TestIntake.test_run_intake_normalizes_non_string_id",
      "TestIntake.test_run_intake_strips_whitespace_in_id",
      "TestIntake.test_run_intake_dedups_by_id_after_normalization"]),

    ("M12", "空白だけの id をキーごと落とさず残す", PKG / "model.py",
     "            rid = str(rid).strip()\n            if rid:\n                spec[\"id\"] = rid\n",
     "            spec[\"id\"] = str(rid)\n",
     ["TestIntake.test_parse_intake_records_normalizes_title_and_id",
      "TestIntake.test_run_intake_blank_id_falls_back_to_generated_id"]),

    ("M13", "title を strip せず生のまま spec へ入れる", PKG / "model.py",
     '        spec = {"title": title}\n',
     '        spec = {"title": raw.get("title")}\n',
     ["TestIntake.test_parse_intake_records_normalizes_title_and_id"]),
]


def run(tests):
    p = subprocess.run([sys.executable, "tests/test_agent_project.py", *tests],
                       cwd=str(ROOT), capture_output=True, text=True,
                       env={**__import__("os").environ, "PYTHONPATH": "."})
    return p.returncode, (p.stderr or "").strip().splitlines()[-1:]


rows = []
for mid, what, path, before, after, tests in MUTATIONS:
    original = path.read_text(encoding="utf-8")
    if original.count(before) != 1:
        rows.append((mid, what, "SKIP", f"置換前テキストが {original.count(before)} 件（1 件でない）"))
        continue
    try:
        path.write_text(original.replace(before, after), encoding="utf-8")
        rc, tail = run(tests)
    finally:
        path.write_text(original, encoding="utf-8")
        assert path.read_text(encoding="utf-8") == original, f"{path} の復元に失敗"
    rows.append((mid, what, "赤（期待どおり）" if rc != 0 else "緑（空振り！）",
                 " / ".join(tests) + " → " + (tail[0] if tail else "?")))

width = max(len(r[1]) for r in rows)
for mid, what, verdict, detail in rows:
    print(f"{mid:<4} {what:<{width}}  {verdict}\n     {detail}")
bad = [r[0] for r in rows if not r[2].startswith("赤")]
print("\n" + ("全ミューテーションで赤" if not bad else f"空振り/実行不能: {', '.join(bad)}"))
sys.exit(1 if bad else 0)
