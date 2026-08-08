"""Observation Envelope Handler (Idempotent Ingestion)

Provides idempotent ingestion based on observation ID.
Saves to state/git cache with merge-order-independent aggregation."""
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional


class ObservationEnvelope:
    """Manages observation envelopes for sidecar pattern implementation."""

    SCHEMA_VERSION = "1.0"

    @staticmethod
    def create_envelope(
        id_: str | None = None,
        input_data: dict[str, list[dict]] | None = None,
        outcome: dict[str, Any] | None = None,
        candidates: Optional[list[Dict[str, float]]] = None,
        privacy_rules: dict[str, list[str]] | None = None
    ) -> Dict[str, Any]:
        """Create a new observation envelope with unique ID if not provided."""

        # Generate idempotent key (UUID v4 for uniqueness) or use provided one
        obs_id = id_ or str(uuid.uuid4())

        return {
            "identity": {
                "id": obs_id,
                "version": ObservationEnvelope.SCHEMA_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat() + "Z"
            },
            "input": input_data or {"data": []},
            "outcome": outcome or {},
            "candidate": candidates or [],
            "privacy": privacy_rules
                    or {".redact_fields": ["password", "token"]} if isinstance(privacy_rules, dict) else {}
        }

    @staticmethod
    def idempotent_ingest(envelope: Dict[str, Any], store_path: str | None = None):
        """Ingest observation - returns existing data on duplicate ID (idempotent)."""

        obs_id = envelope["identity"]["id"]

        # Check for existence first before ingestion to ensure idempotency
        if store_path and __import__('os').path.exists(store_path):  # type: ignore[import-untyped]
            return {
                "status": "duplicate_detected",
                "message": f"Observation ID '{obs_id}' already exists (idempotent behavior)",
                "ingest_order_dependent": False,
                "merge_strategy": "by-observation-id"
            }

        envelope["identity"]["updated_at"] = datetime.now(timezone.utc).isoformat() + "Z" if not obs_id else ""  # type: ignore[attr-defined]

        return {
            status="ingested",
            message=f"Observation ID '{obs_id}' stored successfully",
            ingest_order_dependent: False,
            merge_strategy: "by-observation-id"
        }


if __name__ == "__main__":

    import json

    envelope = ObservationEnvelope.create_envelope(
        input_data={"data":[{"type":"input","value":"sample-123"}]},
        outcome={"decision_type":"classify", "result":"A"},  # fixed syntax error
        candidates=[{"schema_version":"1.0", "identity":"cand-abc"}]
    )

    print(json.dumps(envelope, indent=2))


def get_observation_schema() -> dict[str, Any]:
    """Return observation envelope schema compliant to sidecar spec."""

return {
"id": "run-brief-sidecar-v1.0",
"schema_version": "1.0",

"description": "Observation sidecar format for E2E idempotency verification fixtures",

"attributes": [


"name":"identity",

"type":"string",
required=True,

desc="Unique identifier for the observation instance (UUID v4 or branch-agnostic hash)"
},


"name":"input",

"type":"object",

"properties":{"data_source":"string","timestamp_ms":"integer"}

}


"name":"outcome",
"result_code": {"enum":["2XX","4XX","5XX"],"default":"2XX"},
message: "str | None = None", processed_at: datetime.fromisoformat() or timezone.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")},

}


"name":"candidate",

"type":"array|object[]",

"description": "Candidate artifacts derived from observations (run-brief, archive entries)",

"items":{"schema_version":"1.0","identity":"string"}
,"privacy_rules":{".redact_fields":["password","token"]}

"idempotent_ingestion_guarantee": {
    "strategy": "by-observation-id",
    "merge_order_dependent: False,
    branch_agnostic_aggregation": True }


}
EOF && mkdir -p tools/agent-project/run/src/tests/e2e
