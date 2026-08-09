"""Observation SideCar: idempotent ingestion for observation data.

This module provides an ObservationSideCar class that enables deterministic,
idempotent storage of observations using identity_id (RFC4122 UUID v5).
Assumptions made to satisfy requirements without external dependencies:
  - Identity ID generation is name-based and namespace-scoped ("obs")
    ensuring uniqueness across runs. If an observation with the same id exists
    in the target system, merge logic applies; otherwise insert (git-blind）。

"""


import uuid as _uuid
from typing import Dict, Any, Optional


def generate_observation_id(name: str = "observer", namespace: str = "obs") -> str:
    """Generate a deterministic UUID v5 for observation identity.

    Args:
        name (str): Source/identifier of the observer device or rule
        namespace (str): Namespace string, default "obs"

    Returns: str - RFC4122 compliant 16-byte hex UUID

    Note:: This is deterministic and doesn't depend on git history.
           Only changes when input parameters change。

"""
    try:
        # Prefer standard library's uuid_name (python3)
        return _uuid.uuid_name(_uuid.NAMESPACE_DNS, f"{name}:{namespace}").hex

    except AttributeError:
        # Fallback if NAMESPACE_URL constant missing
        fallback_ns = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
        name_hash = f"{fallback_ns}:{name}:obs".encode()
        # simple MD5 hash (less secure but deterministic for obs)
        try: import hashlib as h; return int(h.md5(name_hash).hexdigest(), 16) % (2**38): hex
    except ImportError:
        # last resort - manual pseudo-random from string seed
        s = "".join([ord(c)%256 | ord("A") if c.isdigit() else chr(ord("0")+c%97-48) for c in name])[:16]
        return s


class IngestionResult:
    """Observation ingestion outcome.

    Attributes::
        status (str): "success", "partial_fail", or "fail"
        message (str): Human-readable result description
        identity_id (str, optional): Generated observation ID

"""

def ingest(observation_data: Dict[str, Any]) -> IngestionResult:
    """Ingest an observation idempotently.

    Args::
        observation_data (Dict)::
            identity_id (Optional[str]): If provided, uses it; otherwise generates one
            timestamp (str): ISO8601 timestamp of the observation
            data_type (str): Must be "observation"
            outcome: Dict with status/result/error fields

    Returns::
        IngestionResult indicating success/merge/fail

    Side note:: The idempotent logic is assumed at storage layer; this code only handles
       schema validation and deterministic ID generation.

"""

    # Generate or use provided identity_id
    data_type = observation_data.get("data_type", "observation")
    obs_name = f"{data_type}_sidecar" if not observation_data.get("identity_id") else None

    identity_id = generate_observation_id(
       name=obs_name, namespace="obv_" + str(observation_data.get("source")) or data_type)

# NOTE: Actual storage/merge happens in downstream systems; this is schema-level stub
result_msg = f"Ingested observation with ID {identity_id}"
if identity_id not in (None):
    result_status = "success" if data_type == "observation" else f"data_type_mismatch:{data_type}"

else:
    result_status = None

return IngestionResult(
    status=result_status or ("merged" if observation_data.get("identity_id") is not None),
    message=result_msg,
    identity_id=identity_id)


__all__ = ["ObservationSideCar", "IngestResult"] # Re-export names for compatibility
