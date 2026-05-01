"""
Connection config loader for skills.

Loads labeled endpoint/API-key configurations from YAML files.

YAML format (connections.yaml):
    redmine:
      - label: default
        url: https://redmine.example.com
        api_key: ${REDMINE_API_KEY}
      - label: staging
        url: https://staging.redmine.example.com
        api_key: my_staging_key

    jenkins:
      - label: default
        url: https://jenkins.example.com
        user: ${JENKINS_USER}
        token: ${JENKINS_TOKEN}

File search order (first file found per tier wins for that tier):
    Tier 1 – Workspace:
        $SKILL_CONNECTIONS_FILE          (explicit override, uses only this file)
        <cwd or any ancestor>/{agent_dir}/connections.yaml
          where {agent_dir} is inferred from this file's location
          (e.g. .github, .copilot, .claude, .kiro …)
    Tier 2 – Global (agent_dir):
        {agent_dir}/connections.yaml
          ({agent_dir} = 4 levels above this file:
           scripts/ -> skill-name/ -> skills/ -> agent_dir/)

Within each tier, workspace always takes priority over global.
For a given (service, label) pair the first tier that contains a match wins.

Env-var interpolation:
    ${VAR_NAME}  in any string value is replaced with os.environ[VAR_NAME].
    If the variable is not set the placeholder is left as-is.

Usage from a skill script:
    from config_loader import get_connection, list_connections

    conn = get_connection("redmine")            # label="default"
    conn = get_connection("redmine", "staging") # specific label
    conns = list_connections("redmine")         # all labels
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Internal: skill_home resolution
# ---------------------------------------------------------------------------

# Map from the agent's home dir name to the workspace dir name.
# Add entries here when a tool uses a different directory in workspaces vs home.
_AGENT_WORKSPACE_DIR = {
    ".copilot": ".github",  # GitHub Copilot: home=~/.copilot, workspace=.github
}


def _cwd_skill_home() -> Path | None:
    """Walk up from cwd to find skill_home using the same agent-dir/skills-dir
    pattern as this file's location, applying workspace-dir remapping where needed.

    Examples
    --------
    File at ~/.copilot/skills/redmine-use/scripts/config_loader.py
      -> searches cwd ancestors for .github/skills/   (remapped)

    File at {workspace}/.github/skills/redmine-use/scripts/config_loader.py
      -> searches cwd ancestors for .github/skills/   (no remap needed)
    """
    file_sh = _file_skill_home()
    skills_dir_name = file_sh.name        # e.g. "skills"
    agent_dir_name  = file_sh.parent.name # e.g. ".github", ".copilot", ".claude"
    ws_agent_dir    = _AGENT_WORKSPACE_DIR.get(agent_dir_name, agent_dir_name)

    for ancestor in [Path.cwd(), *Path.cwd().parents]:
        candidate = (
            ancestor / ws_agent_dir / skills_dir_name
            if ws_agent_dir.startswith(".")
            else ancestor / skills_dir_name
        )
        if candidate.is_dir():
            return candidate
    return None


def _file_skill_home() -> Path:
    """Skill_home derived from this file's location.

    This file lives at: {skill_home}/{skill-name}/scripts/config_loader.py
    So skill_home = parent.parent.parent
    """
    return Path(__file__).resolve().parent.parent.parent


def _skill_home() -> Path:
    """Return skill_home: cwd-derived workspace first, then file-based fallback.

    When the cwd is inside a workspace that has .github/skills/, that directory
    is returned so the workspace {agent_dir}/connections.yaml takes priority
    over the user-home one.  Falls back to the file-based path when no
    workspace structure is found.
    """
    return _cwd_skill_home() or _file_skill_home()


# ---------------------------------------------------------------------------
# Internal: YAML loading (requires pyyaml)
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        print(
            "ERROR: pyyaml が必要です。以下のコマンドでインストールしてください:\n"
            "  pip install pyyaml",
            file=sys.stderr,
        )
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Internal: env-var interpolation
# ---------------------------------------------------------------------------

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _expand_env(value: str) -> str:
    """Replace ${VAR_NAME} with os.environ values. Unknown vars are left as-is."""
    def _replace(m: re.Match) -> str:
        return os.environ.get(m.group(1), m.group(0))
    return _ENV_PATTERN.sub(_replace, value)


def _expand_dict(d: dict) -> dict:
    return {k: _expand_env(v) if isinstance(v, str) else v for k, v in d.items()}


# ---------------------------------------------------------------------------
# Internal: config file discovery
# ---------------------------------------------------------------------------

def _find_config_files() -> list[Path]:
    """
    Return an ordered list of connections.yaml paths to search.
    Workspace files come first; skill_home (global) comes last.
    """
    # 1. Explicit env var – use only this file
    explicit = os.environ.get("SKILL_CONNECTIONS_FILE")
    if explicit:
        p = Path(explicit)
        if p.exists():
            return [p]
        print(
            f"WARNING: SKILL_CONNECTIONS_FILE が指定されていますが見つかりません: {p}",
            file=sys.stderr,
        )
        return []

    found: list[Path] = []

    # 2. Workspace agent_dir (cwd-based, pattern inferred from file path)
    ws_skill_home = _cwd_skill_home()
    if ws_skill_home:
        ws_cfg = ws_skill_home.parent / "connections.yaml"
        if ws_cfg.exists():
            found.append(ws_cfg)

    # 3. Global agent_dir (file-based fallback)
    global_cfg = _file_skill_home().parent / "connections.yaml"
    if global_cfg.exists() and global_cfg not in found:
        found.append(global_cfg)

    return found


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_connection(service: str, label: str = "default") -> dict:
    """
    Return the connection config dict for *service* / *label*.

    Searches config files in priority order (workspace > global) and returns
    the first matching entry with env-var values expanded.
    Returns an empty dict when no match is found.

    Parameters
    ----------
    service : str
        Top-level key in connections.yaml (e.g. ``"redmine"``, ``"jenkins"``).
    label : str
        Entry label (default: ``"default"``).
    """
    for config_file in _find_config_files():
        data = _load_yaml(config_file)
        entries = data.get(service)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("label", "default").lower() == label.lower():
                result = _expand_dict({k: v for k, v in entry.items() if k != "label"})
                return result
    return {}


def list_connections(service: str) -> list[dict]:
    """
    Return all labeled connection configs for *service*, merged across all
    config files.  Workspace entries shadow global entries with the same label.
    """
    seen_labels: set[str] = set()
    results: list[dict] = []

    for config_file in _find_config_files():
        data = _load_yaml(config_file)
        entries = data.get(service)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            lbl = entry.get("label", "default").lower()
            if lbl not in seen_labels:
                seen_labels.add(lbl)
                results.append(_expand_dict(entry))

    return results


def get_config_file_paths() -> list[Path]:
    """Return the list of config files currently in use (for diagnostics)."""
    return _find_config_files()


def get_yaml_write_path() -> Path:
    """Return the path to write connections.yaml to.

    Uses the workspace agent_dir when cwd is inside a workspace; otherwise
    falls back to the global agent_dir derived from this file's location.
    The file does not need to exist.
    """
    ws_sh = _cwd_skill_home()
    if ws_sh:
        return ws_sh.parent / "connections.yaml"
    return _file_skill_home().parent / "connections.yaml"
