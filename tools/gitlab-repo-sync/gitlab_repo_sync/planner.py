# -*- coding: utf-8 -*-
"""判定コア — 「この ref をどちらへ、どう動かすか」を決める純粋関数だけを置く。

git もネットワークも設定ファイルも触らないので、単体テストで全分岐を踏める。
同期の安全性はここの正しさに乗っているので、副作用のあるコードを混ぜないこと。

用語: ペアの 2 辺を a / b と呼ぶ。どちらが「正」かは戦略が決める。
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# アクション種別
# --------------------------------------------------------------------------- #

NOOP = "noop"                   # 両側同一
CREATE_ON_A = "create_a"        # b にだけ在る ref を a へ作成
CREATE_ON_B = "create_b"        # a にだけ在る ref を b へ作成
PUSH_TO_A = "ff_a"              # b だけ進行 → a へ fast-forward
PUSH_TO_B = "ff_b"              # a だけ進行 → b へ fast-forward
FORCE_TO_A = "force_a"          # 一方向 + allow_force のときだけ
FORCE_TO_B = "force_b"          # 一方向 + allow_force のときだけ
DELETE_ON_A = "delete_a"        # b で消えた ref を a からも削除
DELETE_ON_B = "delete_b"        # a で消えた ref を b からも削除
DIVERGED = "diverged"           # 双方進行 → 自動同期しない（人手へ）
SKIPPED = "skipped"             # 戦略の対象外
CONFLICT = "conflict"           # 複数ペアが同じ ref を別々の SHA へ書こうとした
COMPARE = "compare"             # 内部用: merge-base を見ないと決まらない

# 実際にリモートを書き換えるアクション
WRITE_ACTIONS = frozenset({
    CREATE_ON_A, CREATE_ON_B, PUSH_TO_A, PUSH_TO_B,
    FORCE_TO_A, FORCE_TO_B, DELETE_ON_A, DELETE_ON_B,
})
# a 側 / b 側のどちらを書くか
A_WRITE_ACTIONS = frozenset({CREATE_ON_A, PUSH_TO_A, FORCE_TO_A, DELETE_ON_A})
B_WRITE_ACTIONS = frozenset({CREATE_ON_B, PUSH_TO_B, FORCE_TO_B, DELETE_ON_B})
DELETE_ACTIONS = frozenset({DELETE_ON_A, DELETE_ON_B})
FORCE_ACTIONS = frozenset({FORCE_TO_A, FORCE_TO_B})

# 同期戦略
BIDIRECTIONAL = "bidirectional-ff"
A_TO_B = "a-to-b"
B_TO_A = "b-to-a"
STRATEGIES = (BIDIRECTIONAL, A_TO_B, B_TO_A)

SIDE_A = "a"
SIDE_B = "b"


@dataclass
class RefPlan:
    """ref 1 本ぶんの計画と結果。"""
    ref: str
    action: str
    a_sha: str = None
    b_sha: str = None
    ok: bool = True
    detail: str = ""

    def target_side(self):
        """このアクションが書き換える側。書かないアクションなら None。"""
        if self.action in A_WRITE_ACTIONS:
            return SIDE_A
        if self.action in B_WRITE_ACTIONS:
            return SIDE_B
        return None

    def target_sha(self):
        """書き込み後に対象側が指すことになる SHA（削除なら None）。"""
        if self.action in DELETE_ACTIONS:
            return None
        if self.action in A_WRITE_ACTIONS:
            return self.b_sha
        if self.action in B_WRITE_ACTIONS:
            return self.a_sha
        return None


# --------------------------------------------------------------------------- #
# ref の絞り込み
# --------------------------------------------------------------------------- #

def ref_in_scope(ref, include, exclude):
    """ref が同期対象か。exclude が include より優先。

    include が空なら「対象なし」を返す（設定漏れで全 ref を流し始めるより、
    何も同期しない方が安全）。パターンは fnmatch の glob。
    """
    if any(fnmatch.fnmatch(ref, pat) for pat in exclude):
        return False
    return any(fnmatch.fnmatch(ref, pat) for pat in include)


# --------------------------------------------------------------------------- #
# 素の判定（merge-base 無しで分かるところまで）
# --------------------------------------------------------------------------- #

def classify_ref(a_sha, b_sha, prev_a, prev_b, propagate_deletes):
    """両側の現在 SHA と前回同期時の SHA から、方向を決める前の判定を返す。

    戻り値: NOOP / CREATE_ON_A / CREATE_ON_B / DELETE_ON_A / DELETE_ON_B / COMPARE

    削除は「無い」という事実だけでは新規未作成と区別できないので、前回のスナップショットが要る。
    前回は両側に在った ref が今は片側から消えていて、**残っている側が前回から動いていない**
    ときだけ削除とみなす。残っている側が進んでいるなら、削除より後の作業が載っている可能性が
    あるので削除は伝播せず、作成し直す方に倒す（消す方の誤りは取り返しがつかない）。
    """
    if a_sha == b_sha:
        return NOOP
    if a_sha is not None and b_sha is None:
        deleted_on_b = (propagate_deletes and prev_b is not None and prev_a == a_sha)
        return DELETE_ON_A if deleted_on_b else CREATE_ON_B
    if a_sha is None and b_sha is not None:
        deleted_on_a = (propagate_deletes and prev_a is not None and prev_b == b_sha)
        return DELETE_ON_B if deleted_on_a else CREATE_ON_A
    return COMPARE


def resolve_compare(a_sha, b_sha, merge_base, is_tag=False):
    """両側に在って SHA が違う ref を merge-base で決着させる。

    タグは「動かないもの」として扱い、指し先が違えば ff 判定をせず diverged にする。
    タグの付け替えを自動で伝播させると、片側が指していたコミットが静かに参照されなくなる。
    """
    if is_tag:
        return DIVERGED
    if merge_base is None:
        return DIVERGED                 # 共通祖先が無い（無関係な履歴）
    if merge_base == b_sha:
        return PUSH_TO_B                # b は a の祖先 = a だけ進行
    if merge_base == a_sha:
        return PUSH_TO_A                # a は b の祖先 = b だけ進行
    return DIVERGED


# --------------------------------------------------------------------------- #
# 戦略の適用
# --------------------------------------------------------------------------- #

def apply_strategy(action, strategy, allow_force=False, propagate_deletes=False):
    """素の判定にルールの戦略を当てて、最終アクションを決める。

    - bidirectional-ff : ff で解決できるものだけ同期する。分岐は diverged のまま。
                         **allow_force を立てても force はしない**（どちらが正か決まって
                         いない関係で強制上書きすると、勝った側の都合で他方の作業が消える）。
    - a-to-b           : a を正とし、b にだけ書く。allow_force なら b が進行/分岐していても
                         強制上書きする（＝ミラー運用）。
    - b-to-a           : その逆。
    """
    if strategy == BIDIRECTIONAL:
        return action
    if strategy == A_TO_B:
        return _one_way(action, src_create=CREATE_ON_B, src_push=PUSH_TO_B,
                        src_delete=DELETE_ON_B, dst_push=PUSH_TO_A,
                        dst_delete=DELETE_ON_A, dst_create=CREATE_ON_A,
                        force=FORCE_TO_B, allow_force=allow_force,
                        propagate_deletes=propagate_deletes)
    if strategy == B_TO_A:
        return _one_way(action, src_create=CREATE_ON_A, src_push=PUSH_TO_A,
                        src_delete=DELETE_ON_A, dst_push=PUSH_TO_B,
                        dst_delete=DELETE_ON_B, dst_create=CREATE_ON_B,
                        force=FORCE_TO_A, allow_force=allow_force,
                        propagate_deletes=propagate_deletes)
    raise ValueError("unknown strategy: %s" % strategy)


def _one_way(action, src_create, src_push, src_delete, dst_push, dst_delete,
             dst_create, force, allow_force, propagate_deletes):
    """一方向戦略の共通処理。「正」の側を src、書かれる側を dst と呼ぶ。

    src_create / src_push / src_delete は dst を書くアクション（＝許可される）。
    dst_push / dst_delete / dst_create は src を書くアクション（＝許可されない）。
    """
    if action in (src_create, src_push, src_delete):
        return action
    if action == dst_delete:
        # dst 側で消された ref。src が正なので、削除を取り込まず dst へ復元する。
        return src_create
    if action in (dst_push, DIVERGED):
        # dst が勝手に進んだ／分岐した。ミラー運用なら src で上書き、そうでなければ触らない。
        return force if allow_force else SKIPPED
    if action == dst_create:
        # dst にしか無い ref。掃除するのは「force かつ削除伝播あり」を明示したときだけ。
        return src_delete if (allow_force and propagate_deletes) else SKIPPED
    return action


# --------------------------------------------------------------------------- #
# ペアをまたいだ書き込み衝突
# --------------------------------------------------------------------------- #

def detect_write_conflicts(writes):
    """同じ (リポジトリ, ref) を複数ペアが別々の SHA へ書こうとしていないか調べる。

    引数 writes は (repo_slug, ref, sha_or_None, tag) のリスト。tag は呼び出し側が
    衝突を報告するための目印（ペア名など）。
    戻り値: {(repo_slug, ref): [tag, ...]} — 衝突したものだけ。

    3 つ以上のリポジトリを鎖状にペアで繋いだ設定（A⇔B, B⇔C）で、A と C が別々に
    進んでいると B へ 2 通りの書き込みが同時に発生する。片方を黙って後勝ちにすると
    実行順で結果が変わるので、両方とも止めて人へ出す。
    """
    seen = {}
    for repo_slug, ref, sha, tag in writes:
        seen.setdefault((repo_slug, ref), []).append((sha, tag))
    conflicts = {}
    for key, entries in seen.items():
        if len({sha for sha, _ in entries}) > 1:
            conflicts[key] = [tag for _, tag in entries]
    return conflicts
