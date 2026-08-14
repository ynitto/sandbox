import datetime as dt
from pathlib import Path

import qualification_seed


ARCHIVE = Path(__file__).parent / "results" / "archive"


def _candidate(document, agent_cli, model):
    return next(
        item for item in document["candidates"]
        if item["agent_cli"] == agent_cli and item["model"] == model
    )


def test_archive_measurements_become_versioned_qualifications():
    generated_at = dt.datetime(2026, 8, 15, tzinfo=dt.timezone.utc)
    document = qualification_seed.build_seed(ARCHIVE, generated_at=generated_at, revision=1)

    aider = _candidate(document, "aider", "gemma4:e4b")
    repair = aider["qualifications"]["existing-test-repair"]
    assert (repair["samples"], repair["passed"], repair["status"]) == (9, 9, "qualified")
    assert repair["source"] == "eval-archive"
    assert repair["valid_until"] == "2026-11-13T00:00:00Z"

    e4b = _candidate(document, "ollama", "gemma4:e4b")
    assert e4b["qualifications"]["extract"]["status"] == "qualified"
    assert e4b["qualifications"]["bounded-analysis"]["status"] == "qualified"
    assert e4b["qualifications"]["bounded-review"]["status"] == "blocked"

    reviewer = _candidate(document, "ollama-verify", "gemma4:12b")
    review = reviewer["qualifications"]["bounded-review"]
    assert (review["samples"], review["passed"], review["status"]) == (6, 6, "qualified")
    assert document["evaluation_profiles"][review["evaluation_profile_id"]]["valid_for_days"] == 90
