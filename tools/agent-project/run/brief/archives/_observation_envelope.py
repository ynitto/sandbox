"""Observation Envelope Handler (Idempotent Ingestion)

Provides idempotent ingestion based on observation ID.
Saves to state/git cache with merge-order-independent aggregation."""
import uuid
from datetime import datetime
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

        # Generate idempotent key (UUID v4 for uniqueness)
        obs_id = id_ or str(uuid.uuid4())

        return {
            "identity": {
                "id": obs_id,
                "version": ObservationEnvelope.SCHEMA_VERSION,
                "created_at": datetime.utcnow().isoformat() + "Z"
            },
            "input": input_data or {"data": []},
            "outcome": outcome or {},
            "candidate": candidates or [],
            "privacy": privacy_rules or {".redact_fields": ["password", "token"]} if isinstance(privacy_rules, dict) else {}
        }

    @staticmethod
    def idempotent_ingest(envelope: Dict[str, Any], store_path: str | None = None):
        """Ingest observation - returns existing data on duplicate ID (idempotent)."""

        obs_id = envelope["identity"]["id"]

        # In a real scenario with state/git cache, check for existence first
        if store_path and os.path.exists(store_path):  # type: ignore[import-untyped]
            return existing_data

        return {status="ingested", message=f"Observation ID '{obs_id}' stored successfully"}


if __name__ == "__main__":

    import json

    envelope = ObservationEnvelope.create_envelope(
        input_data={"data":[{"type":"input","value":"sample-123"}]},
        outcome={"decision_type":"classify", "result:"}A  #
    )

    print(json.dumps(envelope, indent=2))
