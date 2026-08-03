# -*- coding: utf-8 -*-
"""共有オブジェクトストア — git の実行を担う唯一の層。

1 グループ（ペアで繋がったリポジトリ群）につき bare リポジトリを 1 つ持ち、各リポジトリの
ref を `refs/sync/<slug>/…` へ分けて格納する。同じリポジトリが何組のペアに出てきても、
clone もオブジェクトも 1 つで済む。merge-base も同一リポジトリ内の計算になるので、
比較のためにワークツリーを作る必要がない。

ネットワークに出るのは ls_remote / fetch / push の 3 つだけ。いずれも refspec を
まとめて 1 コマンドで叩くので、リポジトリあたりの接続回数は 1 実行につき最大 3 回に収まる。
"""
from __future__ import annotations

import os
import subprocess


class GitError(RuntimeError):
    """git コマンドの失敗。メッセージは Logger を通して伏せ字にしてから出すこと。"""


def git_env():
    """無人実行で認証プロンプトに捕まらないようにした環境変数。"""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env.setdefault("GIT_ASKPASS", "echo")
    return env


def run_git(args, cwd=None, timeout=900, check=True):
    """git を単発・有界で実行して (returncode, stdout, stderr) を返す。"""
    try:
        proc = subprocess.run(
            ["git"] + list(args), cwd=cwd, timeout=timeout, env=git_env(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.TimeoutExpired:
        raise GitError("git %s がタイムアウトしました（%d 秒）" % (args[0], timeout))
    except FileNotFoundError:
        raise GitError("git コマンドが見つかりません。PATH を確認してください。")
    if check and proc.returncode != 0:
        raise GitError("git %s が失敗しました (%d): %s"
                       % (" ".join(args[:2]), proc.returncode, proc.stderr.strip()))
    return proc.returncode, proc.stdout, proc.stderr


def sync_ref(slug, ref):
    """リモートの ref をストア内のどこへ置くか。

    refs/heads/main -> refs/sync/<slug>/heads/main
    """
    return ref.replace("refs/", "refs/sync/%s/" % slug, 1)


def parse_ls_remote(output):
    """`git ls-remote` の出力を {ref: sha} にする。

    注釈つきタグの peel 行（`refs/tags/v1^{}`）は捨てる。タグは「タグオブジェクトごと」
    同期するので、指し先のコミットではなくタグ自身の SHA を比べる必要がある。
    """
    refs = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        sha, ref = parts
        if ref.endswith("^{}"):
            continue
        refs[ref] = sha
    return refs


def parse_push_porcelain(output):
    """`git push --porcelain` の出力を {dst_ref: (ok, detail)} にする。

    行の形式は "<flag>\\t<src>:<dst>\\t<summary>"。flag が '!' なら拒否、
    それ以外（' ' / '+' / '*' / '-' / '='）は成功。まとめて push した中の 1 本だけが
    拒否されることがあるので、終了コードではなく行単位で成否を見る。
    """
    results = {}
    for line in output.splitlines():
        if "\t" not in line:
            continue                       # "To <url>" / "Done" などの行
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        flag, ref_pair = parts[0], parts[1]
        summary = parts[2] if len(parts) > 2 else ""
        dst = ref_pair.split(":")[-1]
        results[dst] = (flag != "!", summary or flag or "ok")
    return results


class Store:
    """1 グループぶんの共有ストア。"""

    def __init__(self, path, log, timeout=900):
        self.path = path
        self.log = log
        self.timeout = timeout

    def _git(self, args, check=True):
        self.log.debug("git %s" % " ".join(args))
        return run_git(args, cwd=self.path, timeout=self.timeout, check=check)

    def ensure(self):
        """bare リポジトリを用意する（冪等）。"""
        if os.path.isdir(os.path.join(self.path, "objects")):
            return False
        os.makedirs(self.path, exist_ok=True)
        run_git(["init", "--bare", "-q", self.path], timeout=self.timeout)
        # remote は登録しない。URL に PAT を埋めて渡すので、.git/config に書き残さない。
        return True

    def ls_remote(self, url):
        """リモートの ref 一覧を 1 コマンドで取得する（履歴は転送しない）。"""
        _, out, _ = self._git(["ls-remote", "--heads", "--tags", url])
        return parse_ls_remote(out)

    def has_object(self, sha):
        """そのオブジェクトが既にストアにあるか（あれば fetch しない）。"""
        if not sha:
            return False
        rc, _, _ = self._git(["cat-file", "-e", "%s^{object}" % sha], check=False)
        return rc == 0

    def fetch(self, url, slug, refs):
        """指定 ref だけを 1 コマンドでまとめて取得する（全 ref 総なめはしない）。"""
        refs = list(refs)
        if not refs:
            return
        refspecs = ["+%s:%s" % (r, sync_ref(slug, r)) for r in refs]
        self._git(["fetch", "--no-tags", "-q", url] + refspecs)

    def merge_base(self, a_sha, b_sha):
        rc, out, _ = self._git(["merge-base", a_sha, b_sha], check=False)
        return out.strip() if rc == 0 else None

    def push(self, url, refspecs, dry_run=False):
        """まとめて push し、refspec ごとの成否を返す。

        `--force` は refspec 先頭の '+' を付けたものにだけ効く。付いていない push を
        git が非 fast-forward として拒否するのが最後の安全弁で、判定側にバグがあっても
        分岐した履歴を上書きしない。
        """
        refspecs = list(refspecs)
        if not refspecs:
            return {}
        args = ["push", "--porcelain"]
        if dry_run:
            args.append("--dry-run")
        rc, out, err = self._git(args + [url] + refspecs, check=False)
        results = parse_push_porcelain(out)
        if not results and rc != 0:
            # 1 本も結果行が出ていない = 接続・認証など push 以前の失敗。
            raise GitError("push に失敗しました: %s" % (err.strip() or out.strip()))
        return results

    def gc(self, aggressive=False):
        """ストアの肥大化を抑える（任意。長期運用で呼ぶ）。"""
        args = ["gc", "--quiet"]
        if aggressive:
            args.append("--aggressive")
        self._git(args, check=False)
