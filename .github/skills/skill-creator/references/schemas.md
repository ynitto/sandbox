# JSON Schemas

JSON structures used by skill-creator across different modes.

## evals.json

Located at `evals/evals.json`. Defines test cases for skill evaluation.

```json
{
  "skill_name": "my-skill",
  "evals": [
    {
      "id": 1,
      "prompt": "The task prompt given to the executor",
      "expected_output": "Human-readable description of what success looks like",
      "files": ["path/to/input/file.pdf"],
      "assertions": [
        "The output file exists and is non-empty",
        "The output contains the extracted table data",
        "All rows from the input are represented"
      ]
    }
  ]
}
```

Fields:
- `id`: Unique integer identifier
- `prompt`: The task to execute
- `expected_output`: Human-readable description of success
- `files`: Optional input file paths
- `assertions`: List of verifiable statements (added after initial run)

## history.json

Tracks version progression in Improve mode.

```json
{
  "started_at": "2026-01-15T10:30:00Z",
  "skill_name": "my-skill",
  "best_version": "iteration-2",
  "iterations": [
    {
      "version": "iteration-1",
      "pass_rate": 0.67,
      "passed": 2,
      "failed": 1,
      "total": 3,
      "grading_summary": {}
    }
  ]
}
```

## grading.json

Output from the grader agent. Saved to each run directory.

```json
{
  "expectations": [
    {
      "text": "The output file exists and is non-empty",
      "passed": true,
      "evidence": "Found output.pdf (24KB) in outputs/"
    },
    {
      "text": "All rows from the input are represented",
      "passed": false,
      "evidence": "Output has 45 rows but input had 52 rows"
    }
  ],
  "summary": {
    "passed": 1,
    "failed": 1,
    "total": 2,
    "pass_rate": 0.5
  },
  "execution_metrics": {
    "tool_calls": {
      "Read": 3,
      "Write": 2,
      "Bash": 5
    },
    "total_tool_calls": 10,
    "total_steps": 4,
    "errors_encountered": 0,
    "output_chars": 8500,
    "transcript_chars": 2100
  },
  "timing": {
    "executor_duration_seconds": 142.0,
    "grader_duration_seconds": 18.0,
    "total_duration_seconds": 160.0
  },
  "claims": [
    {
      "claim": "Extracted 45 rows from the PDF table",
      "type": "factual",
      "verified": false,
      "evidence": "Input had 52 rows; 7 rows are missing"
    }
  ],
  "user_notes_summary": {
    "uncertainties": [],
    "needs_review": [],
    "workarounds": []
  },
  "eval_feedback": {
    "suggestions": [],
    "overall": "No suggestions, evals look solid"
  }
}
```

**Important**: The viewer reads `expectations[].text`, `expectations[].passed`, and `expectations[].evidence` exactly. Do not use `name`/`met`/`details` or other field names.

## metrics.json

Executor agent output. Saved to `outputs/metrics.json`.

```json
{
  "tool_calls": {
    "Read": 3,
    "Write": 2,
    "Bash": 5,
    "Glob": 1
  },
  "total_tool_calls": 11,
  "total_steps": 4,
  "output_files": ["output.pdf", "summary.txt"],
  "errors_encountered": 0,
  "output_chars": 8500,
  "transcript_chars": 2100
}
```

## timing.json

Wall clock timing for a run. Saved to the run directory (sibling to `outputs/`).

```json
{
  "total_tokens": 84852,
  "duration_ms": 142000,
  "total_duration_seconds": 142.0,
  "started_at": "2026-01-15T10:31:00Z",
  "completed_at": "2026-01-15T10:33:22Z"
}
```

## benchmark.json

Output from `aggregate_benchmark.py`. The viewer reads these field names exactly.

```json
{
  "metadata": {
    "skill_name": "my-skill",
    "skill_path": "path/to/skill",
    "executor_model": "claude-sonnet-4-6",
    "analyzer_model": "claude-sonnet-4-6",
    "timestamp": "2026-01-15T10:30:00Z",
    "evals_run": [0, 1, 2],
    "runs_per_configuration": 3
  },
  "runs": [
    {
      "eval_id": 0,
      "configuration": "with_skill",
      "run_number": 1,
      "result": {
        "pass_rate": 1.0,
        "passed": 3,
        "failed": 0,
        "total": 3,
        "time_seconds": 142.0,
        "tokens": 84852,
        "tool_calls": 11,
        "errors": 0
      },
      "expectations": [
        {"text": "...", "passed": true, "evidence": "..."}
      ],
      "notes": []
    }
  ],
  "run_summary": {
    "with_skill": {
      "pass_rate": {"mean": 0.89, "stddev": 0.11, "min": 0.67, "max": 1.0},
      "time_seconds": {"mean": 145.2, "stddev": 12.3, "min": 130.0, "max": 158.0},
      "tokens": {"mean": 82000, "stddev": 3000, "min": 78000, "max": 86000}
    },
    "without_skill": {
      "pass_rate": {"mean": 0.44, "stddev": 0.19, "min": 0.33, "max": 0.67},
      "time_seconds": {"mean": 98.5, "stddev": 8.1, "min": 89.0, "max": 108.0},
      "tokens": {"mean": 61000, "stddev": 2500, "min": 58000, "max": 64000}
    },
    "delta": {
      "pass_rate": "+0.45",
      "time_seconds": "+46.7",
      "tokens": "+21000"
    }
  },
  "notes": [
    "Assertion 'Output is a PDF' passes 100% in both configs — may not differentiate skill value",
    "Eval 2 shows high variance — possible flaky test"
  ]
}
```

Put each `with_skill` version before its baseline counterpart in the `runs` array.

## comparison.json

Output from the blind comparator agent.

```json
{
  "winner": "A",
  "reasoning": "Output A was more complete and better formatted.",
  "rubric": {
    "criteria": [
      {
        "name": "Completeness",
        "description": "All required sections present",
        "score_a": 5,
        "score_b": 3,
        "evidence_a": "All 4 sections present",
        "evidence_b": "Missing executive summary"
      }
    ],
    "total_a": 18,
    "total_b": 14,
    "max_possible": 25
  },
  "quality_summary": {
    "a": {"strengths": ["..."], "weaknesses": ["..."]},
    "b": {"strengths": ["..."], "weaknesses": ["..."]}
  },
  "assertion_results": {
    "a_passed": 4,
    "b_passed": 2,
    "total": 5,
    "details": []
  }
}
```

## analysis.json

Output from the post-hoc analyzer agent.

```json
{
  "comparison_summary": {
    "winner": "A",
    "winner_skill": "path/to/skill-v2",
    "loser_skill": "path/to/skill-v1",
    "comparator_reasoning": "Summary of why A won"
  },
  "winner_strengths": ["Clear step-by-step instructions", "Validation script"],
  "loser_weaknesses": ["Vague instructions", "No validation"],
  "instruction_following": {
    "winner": {"score": 9, "issues": []},
    "loser": {"score": 6, "issues": ["Did not use formatting template"]}
  },
  "improvement_suggestions": [
    {
      "priority": "high",
      "category": "instructions",
      "suggestion": "Add explicit step-by-step process",
      "expected_impact": "Eliminate ambiguity"
    }
  ],
  "transcript_insights": {
    "winner_execution_pattern": "Read skill -> Followed 5-step process -> Validated -> Output",
    "loser_execution_pattern": "Read skill -> Unclear approach -> No validation -> Errors"
  }
}
```
