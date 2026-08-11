"""aider 索引の組み立てを検証する（`python test_install_index.py` か pytest で実行）。

索引は aider が毎回文脈へ載せるファイルなので、太らせない仕掛けが壊れると
「本題の材料が入らない」という形で静かに劣化する（気付けない）。ここで止める。
"""
import install


def _entries(n_core: int, n_other: int, summary: str = "要約") -> list[tuple[str, str, str]]:
    return ([(f"core{i}", "core", summary) for i in range(n_core)]
            + [(f"exp{i}", "experimental", summary) for i in range(n_other)])


def test_no_path_lines() -> None:
    """パス行は書かない（`<skill_home>/<名前>` で導出できる冗長な情報だった）。"""
    body = install.render_aider_skill_index("/home/u/.agents/skills", _entries(2, 2))
    assert "path:" not in body
    assert body.count("/home/u/.agents/skills") == 1, "基準ディレクトリは冒頭で 1 回だけ示す"


def test_tier_decides_granularity() -> None:
    """core / stable は要約付き、それ以外は名前だけ。"""
    body = install.render_aider_skill_index("/base", [
        ("a-core", "core", "コアの要約"),
        ("b-stable", "stable", "安定版の要約"),
        ("c-exp", "experimental", "実験版の要約"),
        ("d-none", "", "tier 無しの要約"),
    ])
    assert "- `a-core` — コアの要約" in body
    assert "- `b-stable` — 安定版の要約" in body
    assert "- `c-exp`" not in body and "実験版の要約" not in body
    assert "`c-exp`, `d-none`" in body, "名前だけの節に並ぶ"


def test_budget_is_a_ceiling_and_keeps_every_name() -> None:
    """予算を超えたら要約を落とす。ただしスキル名は 1 件も消さない。"""
    names = [f"core{i}" for i in range(400)]
    body = install.render_aider_skill_index("/base", _entries(400, 0, "あ" * 60))
    assert len(body.encode("utf-8")) <= install.AIDER_INDEX_BUDGET
    for n in names:
        assert f"`{n}`" in body, f"{n} が索引から消えた（/read で足せなくなる）"


def test_under_budget_keeps_all_summaries() -> None:
    """予算内なら要約を落とさない（削りすぎない）。"""
    body = install.render_aider_skill_index("/base", _entries(5, 5))
    assert body.count("— 要約") == 5


def test_summary_is_first_sentence_only(tmp_path=None) -> None:
    """description は「要約。『トリガー』…」形式。索引に要るのは先頭 1 文だけ。"""
    import pathlib
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        skill = pathlib.Path(d, "s")
        skill.mkdir()
        long_desc = "本題の要約。" + "「トリガー語」" * 40
        (skill / "SKILL.md").write_text(
            f"---\nname: s\ndescription: {long_desc}\nmetadata:\n  tier: core\n---\n本文",
            encoding="utf-8")
        tier, summary = install._skill_meta(str(skill))
    assert tier == "core"
    assert summary == "本題の要約"
    assert len(summary) <= install.AIDER_SUMMARY_CHARS


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("全て通過")
