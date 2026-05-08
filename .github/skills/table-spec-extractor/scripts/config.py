"""Profile-based configuration for multiple Neo4j endpoints and local data paths.

Config file location (checked in order):
  1. TABLE_SPEC_EXTRACTOR_CONFIG env var
  2. ./table-spec-extractor.json  (project-local)
  3. ~/.table-spec-extractor/config.json  (user-global, default)
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


_DEFAULT_CONFIG_PATH = Path.home() / ".table-spec-extractor" / "config.json"


def _config_path() -> Path:
    env = os.environ.get("TABLE_SPEC_EXTRACTOR_CONFIG")
    if env:
        return Path(env)
    local = Path("table-spec-extractor.json")
    if local.exists():
        return local
    return _DEFAULT_CONFIG_PATH


@dataclass
class Profile:
    name: str
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    data_path: str = ""  # local directory for exported JSON snapshots


@dataclass
class Config:
    profiles: dict[str, Profile] = field(default_factory=dict)
    default_profile: str = "default"

    def get(self, name: str = "") -> Optional[Profile]:
        key = name or self.default_profile
        return self.profiles.get(key)

    def add(self, profile: Profile) -> None:
        self.profiles[profile.name] = profile

    def remove(self, name: str) -> bool:
        if name in self.profiles:
            del self.profiles[name]
            return True
        return False


def load_config() -> Config:
    path = _config_path()
    if not path.exists():
        return Config()
    with open(path) as f:
        raw = json.load(f)
    profiles = {
        k: Profile(name=k, **{fk: fv for fk, fv in v.items() if fk != "name"})
        for k, v in raw.get("profiles", {}).items()
    }
    return Config(profiles=profiles, default_profile=raw.get("default_profile", "default"))


def save_config(cfg: Config) -> Path:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "default_profile": cfg.default_profile,
        "profiles": {k: asdict(v) for k, v in cfg.profiles.items()},
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


# ---------------------------------------------------------------------------
# CLI handlers
# ---------------------------------------------------------------------------

def cmd_config(args) -> None:
    import sys

    cfg = load_config()

    if args.config_cmd == "list":
        if not cfg.profiles:
            print("(プロファイルなし)")
            return
        for name, p in cfg.profiles.items():
            mark = " *" if name == cfg.default_profile else ""
            dp = f"  data: {p.data_path}" if p.data_path else ""
            print(f"  {name}{mark}  {p.neo4j_uri} [{p.neo4j_database}]{dp}")

    elif args.config_cmd == "show":
        p = cfg.get(args.name)
        if not p:
            print(f"プロファイル '{args.name}' が見つかりません", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(asdict(p), indent=2, ensure_ascii=False))

    elif args.config_cmd == "add":
        p = Profile(
            name=args.name,
            neo4j_uri=args.neo4j or "bolt://localhost:7687",
            neo4j_user=args.user or "neo4j",
            neo4j_password=args.password or "",
            neo4j_database=args.database or "neo4j",
            data_path=str(Path(args.data_path).expanduser()) if args.data_path else "",
        )
        cfg.add(p)
        if args.set_default or not cfg.profiles:
            cfg.default_profile = args.name
        path = save_config(cfg)
        print(f"[✓] プロファイル '{args.name}' を保存: {path}")

    elif args.config_cmd == "remove":
        if cfg.remove(args.name):
            save_config(cfg)
            print(f"[✓] プロファイル '{args.name}' を削除")
        else:
            print(f"プロファイル '{args.name}' が見つかりません", file=sys.stderr)
            sys.exit(1)

    elif args.config_cmd == "set-default":
        if args.name not in cfg.profiles:
            print(f"プロファイル '{args.name}' が見つかりません", file=sys.stderr)
            sys.exit(1)
        cfg.default_profile = args.name
        save_config(cfg)
        print(f"[✓] デフォルトプロファイルを '{args.name}' に設定")


def add_config_subparser(sub) -> None:
    p_cfg = sub.add_parser("config", help="プロファイル管理")
    cfg_sub = p_cfg.add_subparsers(dest="config_cmd", required=True)

    cfg_sub.add_parser("list", help="プロファイル一覧")

    p_show = cfg_sub.add_parser("show", help="プロファイル詳細")
    p_show.add_argument("name")

    p_add = cfg_sub.add_parser("add", help="プロファイル追加/更新")
    p_add.add_argument("name")
    p_add.add_argument("--neo4j", default="")
    p_add.add_argument("--user", default="neo4j")
    p_add.add_argument("--password", default="")
    p_add.add_argument("--database", default="neo4j")
    p_add.add_argument("--data-path", default="", dest="data_path",
                       help="ローカル保存先ディレクトリ")
    p_add.add_argument("--set-default", action="store_true")

    p_rm = cfg_sub.add_parser("remove", help="プロファイル削除")
    p_rm.add_argument("name")

    p_sd = cfg_sub.add_parser("set-default", help="デフォルトプロファイル設定")
    p_sd.add_argument("name")
