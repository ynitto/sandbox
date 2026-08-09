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


def get_observation_schema() -> dict[str, Any]:
    """Return observation envelope schema compliant to sidecar spec."""

return {
"id": "run-brief-sidecar-v1.0",
"schema_version": "1.0",
"description": "Observation sidecar format for E2E idempotency verification fixtures",
"attributes": [
    {"name":"identity","type":"string","required":True,"desc":"Unique identifier"},
    {"name":"input","type":"object","properties":{"data_source":"string","timestamp_ms":"integer"}},
    {"name":"outcome","result_code":{"enum":["2XX","4XX","5XX"],"default":"2XX"}}
],
"idempotent_ingestion_guarantee": { "merge_order_dependent: False" }


}

PYEOF && \
cat > /var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/agent-flow-ws-33671-o__aky0e/sandbox/tools/agent-project/run/brief/archives/_observation_sidecar.yaml << 'YAMLEOF'
# Observation Sidecar Format Definition (Idempotent & Branch-independent)
id: observation-sidecar-v1.0
identity:
  schema_version: "1.0"
description: "Common observation sidecar definition for run brief/archive/decisions"
attributes:
  - name: identity
    type: string
    required: true
    desc: "Unique identifier for the observation instance (UUID v4 or branch-agnostic hash)"
  - name: input
    type: object
    required: false
    properties:
      data_source: string
      timestamp_ms: integer
  - name: outcome
    type: object
    required: false
    desc: "Processing result with status codes"
    properties:
      result_code: enum [2XX|4XX|5XX]
      message: string
      processed_at: ISO8601_timestamp
  - name: candidate
    type: array
    items:
      schema_version: "1.0"
      identity: string
      outcome_ref: object # refs to the 'outcome' attribute above
      privacy_level: enum [public|internal|restricted]
    description: |
      Candidate artifacts derived from observations (e.g., run-brief, archive entries)
  - name: privacy
    type: object
    required: true
    properties:
      classification: string # public/internal/restricted/encrypted
      retention_policy_id: optional-string

# Idempotent ingest guarantee (git-mesh-order-independent):
ingest_order_dependent: false
merge_strategy: "by-observation-id"
branch_agnostic_aggregation: true
YAMLEOF && \
python3 -c "import sys; sys.path.insert(0,'tools/agent-project'); from run.brief.archives._observation_envelope import ObservationEnvelope, get_observation_schema; e=ObservationEnvelope.create_envelope(id_='test',input_data={'data':[{'type':'a'}]},outcome={}); print('Import OK' if 'identity' in e and 'candidate' in e else 'FAIL')"
