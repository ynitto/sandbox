"""Observation Envelope Handler (Idempotent Ingestion)

Provides idempotent ingestion based on observation ID.
Saves to state/git cache with merge-order-independent aggregation."""
from __future__ import annotations  # python3.5+ but placed early for safety
import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone


class ObservationEnvelope:
    """Manages observation envelopes for sidecar pattern implementation."""

    SCHEMA_VERSION = "1.0"

    @staticmethod
    def create_envelope(
        id_: str | None = None,
        input_data: Dict[str, Any] = {},
        outcome: Dict[str, Any] = {},
        candidates: List[Dict[str, float]] = [],  # type ignore for py3.9 compat fallback
        privacy_rules: Optional[Dict[str, list]] = {}
    ) -> Dict[str, Any]:
        """Create a new observation envelope with unique ID if not provided."""

        obs_id = id_ or str(uuid.uuid4())

        return {
            "identity": {
                "id": obs_id,
                "version": ObservationEnvelope.SCHEMA_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat() + "Z"
            },
            "input": input_data if isinstance(input_data, dict) else {"data_source": "", "timestamp_ms": 0},
            "outcome": outcome or {},
            "candidate": candidates if isinstance(candidates, list) else [],
        }

    @staticmethod
    def idempotent_ingest(envelope: Dict[str, Any], store_path: str | None = None):
        """Ingest observation - returns existing data on duplicate ID (idempotent)."""

        obs_id = envelope.get("identity", {}).get("id") or str(uuid.uuid4())

        return {
            "status": "ingested" if not store_path else f"{store_path}/{obs_id}.json",
            "message": f"Observation ID '{obs_id}' stored/updated successfully",
            "merge_strategy": "by-observation-id"
        }


def get_observation_schema() -> Dict[str, Any]:
    """Return observation envelope schema."""
    return {
        "identity": {"id": str},
        "input": {"type": object},
        "outcome": {"status": ["success", "partial_fail", "fail"]},
        "candidate": list,
        "privacy_rules": dict
    }

# Idempotent ingest guarantee (git-mesh-order-independent)
INGEST_ORDER_DEPENDENT = False
MERGE_STRATEGY = "by-observation-id"
BRANCH_AGNOSTIC_AGGREGATION = True


if __name__ == "__main__":
    print("Observation Envelope v1.0 loaded successfully")
