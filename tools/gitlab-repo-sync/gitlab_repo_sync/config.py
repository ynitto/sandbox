# -*- coding: utf-8 -*-
"""設定の読み込みと、リポジトリの重複排除。

設定の骨格:
  credentials … ホスト単位の PAT 表（**グローバル**。ペアごとに書かない）
  rules       … 名前つきの同期ルール（戦略・ref の allowlist・削除伝播など）
  pairs       … 同期したいリポジトリの組。ルールを名前で参照する

同じリポジトリが複数のペアに出てきても clone は 1 つで済むように、URL を正規化して
リポジトリを同定し、ペアで繋がったリポジトリ群（連結成分）ごとに 1 つの共有オブジェクト
ストアへまとめる。ペアの数ではなく**リポジトリの数**だけ fetch すれば足りる形にするため。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field

from .planner import BIDIRECTIONAL, STRATEGIES

DEFAULT_INCLUDE = ["refs/heads/main", "refs/heads/master",
                   "refs/heads/develop", "refs/heads/release/*", "refs/tags/*"]
DEFAULT_EXCLUDE = ["refs/heads/tmp/*"]

CONFIG_BASENAMES = ["gitlab-repo-sync.yaml", "gitlab-repo-sync.yml", "gitlab-repo-sync.json"]


# --------------------------------------------------------------------------- #
# URL の正規化とリポジトリの同定
# --------------------------------------------------------------------------- #

_SCP_RE = re.compile(r"^(?P<user>[^@/]+@)?(?P<host>[^:/]+):(?P<path>[^/].*)$")
_URL_RE = re.compile(r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*)://"
                     r"(?P<cred>[^/@]+@)?(?P<host>[^/:]+)(?P<port>:\d+)?/(?P<path>.*)$")


def split_url(url):
    """URL を (scheme, host, port, path) に分解する。scp 形式（git@host:path）も受ける。

    分解できないときは (None, "", "", url) を返す（ローカルパスなど）。
    """
    url = url.strip()
    m = _URL_RE.match(url)
    if m:
        return (m.group("scheme").lower(), m.group("host").lower(),
                m.group("port") or "", m.group("path"))
    m = _SCP_RE.match(url)
    if m and not os.path.exists(url):
        return ("ssh", m.group("host").lower(), "", m.group("path"))
    return (None, "", "", url)


def canonical_url(url):
    """同じリポジトリを指す URL を同じ文字列に落とす（重複 clone の判定キー）。

    - 資格情報（user:pass@）を落とす … トークンを変えただけで別リポジトリ扱いにしない
    - ホストは小文字化、末尾の .git と余分な / を除去
    - scheme は残す … http と https を同一視すると、片方だけ到達可能な構成で誤同定する
    """
    scheme, host, port, path = split_url(url)
    if scheme is None:
        # ローカルパス。先頭の / を落とすと絶対パスと相対パスが同じ鍵になるので触らない。
        local = os.path.normpath(os.path.expanduser(path))
        return local[:-4] if local.endswith(".git") else local
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return "%s://%s%s/%s" % (scheme, host, port, path)


def repo_slug(url):
    """リポジトリの短い識別子。ストア内の ref 名とファイル名に使う。

    人が見て分かる部分（ホスト末尾 + パス）に、正規化 URL のハッシュを足す。
    ハッシュだけだと運用時にどれがどれか分からず、名前だけだと別ホストの同名リポジトリが
    衝突する。
    """
    canon = canonical_url(url)
    digest = hashlib.sha1(canon.encode("utf-8")).hexdigest()[:8]
    # 読みやすい部分も**正規化後**から作る。素の URL から作ると、末尾 .git の有無だけで
    # 見た目が変わり、同じリポジトリが 2 つあるように見えてしまう。
    # 長い URL を切り詰めるのではなく、ホストと末尾 2 階層だけを使う（深い階層でも
    # 名前が潰れない。一意性は末尾のハッシュが担保する）。
    parts = [p for p in canon.split("://", 1)[-1].split("/") if p]
    readable = "-".join(parts[:1] + parts[-2:] if len(parts) > 3 else parts)
    readable = re.sub(r"[^a-zA-Z0-9]+", "-", readable).strip("-").lower()
    readable = readable[:48].strip("-") or "repo"
    return "%s-%s" % (readable, digest)


# --------------------------------------------------------------------------- #
# 資格情報（グローバル）
# --------------------------------------------------------------------------- #

@dataclass
class Credential:
    host: str                 # glob 可（"*.example.com" など）
    token: str = ""
    username: str = "oauth2"  # GitLab の PAT は任意のユーザー名で通る。Deploy Token は指定必須


class CredentialStore:
    """ホストから PAT を引くグローバルな表。

    PAT をペアごとに書かせない（同じホストの資格情報が設定内に散ると、失効時に
    全部を漏れなく差し替える作業が発生する）。
    """

    def __init__(self, credentials=()):
        self.credentials = list(credentials)

    def find(self, host):
        import fnmatch
        host = (host or "").lower()
        for cred in self.credentials:
            if fnmatch.fnmatch(host, cred.host.lower()):
                return cred
        return None

    def authenticated_url(self, url):
        """http(s) の URL に PAT を埋め込む。

        ssh やローカルパスは触らない（鍵とエージェントで認証するもので、
        トークンを挟む余地が無い）。既に資格情報が書かれている URL も尊重する。
        """
        scheme, host, _, _ = split_url(url)
        if scheme not in ("http", "https"):
            return url
        m = _URL_RE.match(url.strip())
        if not m or m.group("cred"):
            return url
        cred = self.find(host)
        if not cred or not cred.token:
            return url
        prefix = "%s://" % scheme
        rest = url.strip()[len(prefix):]
        return "%s%s:%s@%s" % (prefix, cred.username, cred.token, rest)

    def secrets(self):
        return [c.token for c in self.credentials if c.token]


# --------------------------------------------------------------------------- #
# ルールとペア
# --------------------------------------------------------------------------- #

@dataclass
class Rule:
    name: str = "default"
    strategy: str = BIDIRECTIONAL
    include: list = field(default_factory=lambda: list(DEFAULT_INCLUDE))
    exclude: list = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    allow_force: bool = False
    propagate_deletes: bool = False


@dataclass
class Pair:
    name: str
    a_url: str                  # 認証情報を埋めていない素の URL（ログ・表示用）
    b_url: str
    rule: Rule = field(default_factory=Rule)
    enabled: bool = True

    def slugs(self):
        return repo_slug(self.a_url), repo_slug(self.b_url)


@dataclass
class Config:
    pairs: list = field(default_factory=list)
    rules: dict = field(default_factory=dict)
    credentials: CredentialStore = field(default_factory=CredentialStore)
    store_dir: str = ""
    state_dir: str = ""
    log_file: str = ""
    git_timeout: int = 900
    lock_timeout_minutes: int = 180
    fail_on_diverged: bool = False

    def pair(self, name):
        for p in self.pairs:
            if p.name == name:
                return p
        return None

    def repos(self):
        """設定に出てくるリポジトリを {slug: url} で返す（重複は 1 つに畳まれる）。"""
        found = {}
        for pair in self.pairs:
            for url in (pair.a_url, pair.b_url):
                found.setdefault(repo_slug(url), url)
        return found


# --------------------------------------------------------------------------- #
# 連結成分（= 共有ストアの単位）
# --------------------------------------------------------------------------- #

@dataclass
class Group:
    """ペアで繋がったリポジトリ群。1 グループ = 1 つの共有オブジェクトストア。"""
    gid: str
    repos: dict = field(default_factory=dict)   # slug -> url
    pairs: list = field(default_factory=list)


def build_groups(pairs):
    """ペアの一覧を連結成分へ分割する（union-find）。

    A⇔B と B⇔C が設定されていれば A/B/C は 1 つのストアを共有する。中身が同じ
    リポジトリ同士なので、オブジェクトを 1 か所に集めればディスクも転送も 1 回で済む。
    無関係なリポジトリまで 1 つのストアに混ぜないのは、巨大化と巻き添え障害を避けるため。
    """
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for pair in pairs:
        a, b = pair.slugs()
        union(a, b)

    buckets = {}
    for pair in pairs:
        a, b = pair.slugs()
        root = find(a)
        group = buckets.setdefault(root, Group(gid=""))
        group.repos[a] = pair.a_url
        group.repos[b] = pair.b_url
        group.pairs.append(pair)

    groups = []
    for group in buckets.values():
        members = sorted(group.repos)
        # グループ ID は所属リポジトリだけから決まる（ペアの追加順で変わるとストアが作り直しになる）。
        digest = hashlib.sha1("\n".join(members).encode("utf-8")).hexdigest()[:8]
        group.gid = "%s-%s" % (members[0].rsplit("-", 1)[0][:32].strip("-") or "group", digest)
        groups.append(group)
    groups.sort(key=lambda g: g.gid)
    return groups


# --------------------------------------------------------------------------- #
# 読み込み
# --------------------------------------------------------------------------- #

def _expand(value):
    """$VAR / ${VAR} を環境変数で展開する（PAT を設定に直書きしたくない場合用）。"""
    return os.path.expandvars(value) if isinstance(value, str) else value


def _path(value, default=""):
    value = _expand(value) or default
    return os.path.expanduser(value) if value else value


def find_config(explicit=""):
    """設定ファイルを探す。--config → カレント → HOME の順。"""
    if explicit:
        return explicit
    for directory in (os.getcwd(), os.path.expanduser("~")):
        for name in CONFIG_BASENAMES:
            candidate = os.path.join(directory, name)
            if os.path.exists(candidate):
                return candidate
    return ""


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml  # type: ignore
        except ImportError:
            raise ConfigError("PyYAML が必要です（pip install --user pyyaml）。"
                              "または JSON 設定を使ってください。")
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ConfigError("設定のトップレベルはマップである必要があります。")
    return build_config(data)


class ConfigError(Exception):
    """設定の誤り。メッセージはそのまま利用者に見せる。"""


def build_config(data):
    """読み込み済みの dict から Config を組み立てる（テストから直接呼べるよう分離）。"""
    home_base = os.path.join(os.path.expanduser("~"), ".gitlab-repo-sync")
    cfg = Config()
    cfg.store_dir = _path(data.get("store_dir"), os.path.join(home_base, "store"))
    cfg.state_dir = _path(data.get("state_dir"), os.path.join(home_base, "state"))
    cfg.log_file = _path(data.get("log_file"))
    cfg.git_timeout = int(data.get("git_timeout", cfg.git_timeout))
    cfg.lock_timeout_minutes = int(data.get("lock_timeout_minutes", cfg.lock_timeout_minutes))
    cfg.fail_on_diverged = bool(data.get("fail_on_diverged", cfg.fail_on_diverged))

    cfg.credentials = CredentialStore(_build_credentials(data.get("credentials")))

    defaults = _build_rule("default", data.get("defaults") or {}, Rule())
    cfg.rules["default"] = defaults
    for name, raw in (data.get("rules") or {}).items():
        if not isinstance(raw, dict):
            raise ConfigError("rules.%s はマップである必要があります。" % name)
        cfg.rules[str(name)] = _build_rule(str(name), raw, defaults)

    raw_pairs = data.get("pairs")
    if not raw_pairs:
        raise ConfigError("設定に pairs が 1 つも定義されていません。")
    seen_names = set()
    for index, raw in enumerate(raw_pairs):
        pair = _build_pair(index, raw, cfg.rules)
        if pair.name in seen_names:
            raise ConfigError("pairs[].name が重複しています: %s" % pair.name)
        seen_names.add(pair.name)
        cfg.pairs.append(pair)
    return cfg


def _build_credentials(raw):
    """credentials はリスト形式（順に評価）とマップ形式（host: token）の両方を受ける。"""
    creds = []
    if raw is None:
        return creds
    if isinstance(raw, dict):
        raw = [dict(entry, host=host) if isinstance(entry, dict) else {"host": host, "token": entry}
               for host, entry in raw.items()]
    if not isinstance(raw, list):
        raise ConfigError("credentials はリストかマップである必要があります。")
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError("credentials[] の要素はマップである必要があります。")
        host = _expand(entry.get("host"))
        if not host:
            raise ConfigError("credentials[].host は必須です。")
        creds.append(Credential(host=str(host),
                                token=_expand(entry.get("token")) or "",
                                username=_expand(entry.get("username")) or "oauth2"))
    return creds


def _build_rule(name, raw, base: Rule):
    strategy = raw.get("strategy", base.strategy)
    if strategy not in STRATEGIES:
        raise ConfigError("rules.%s.strategy が不正です: %s（%s のいずれか）"
                          % (name, strategy, " / ".join(STRATEGIES)))
    return Rule(
        name=name,
        strategy=strategy,
        include=[str(x) for x in raw.get("include", base.include)],
        exclude=[str(x) for x in raw.get("exclude", base.exclude)],
        allow_force=bool(raw.get("allow_force", base.allow_force)),
        propagate_deletes=bool(raw.get("propagate_deletes", base.propagate_deletes)),
    )


def _build_pair(index, raw, rules):
    if not isinstance(raw, dict):
        raise ConfigError("pairs[%d] はマップである必要があります。" % index)
    a_url = _expand(raw.get("a"))
    b_url = _expand(raw.get("b"))
    if not a_url or not b_url:
        raise ConfigError("pairs[%d] には a と b の両方の URL が必要です。" % index)
    name = str(raw.get("name") or repo_slug(a_url))

    rule_name = raw.get("rule", "default")
    base = rules.get(str(rule_name))
    if base is None:
        raise ConfigError("pairs[%s] が参照するルールが未定義です: %s" % (name, rule_name))
    # ペア側にルール項目が直接書かれていれば、参照したルールを上書きする。
    inline = {k: raw[k] for k in ("strategy", "include", "exclude",
                                  "allow_force", "propagate_deletes") if k in raw}
    rule = _build_rule("%s(inline)" % name, inline, base) if inline else base

    if canonical_url(a_url) == canonical_url(b_url):
        raise ConfigError("pairs[%s] の a と b が同じリポジトリを指しています。" % name)
    return Pair(name=name, a_url=str(a_url), b_url=str(b_url), rule=rule,
                enabled=bool(raw.get("enabled", True)))
