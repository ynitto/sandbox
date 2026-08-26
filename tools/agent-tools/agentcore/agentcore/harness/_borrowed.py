"""断片が agent_loop の共有名前空間から借りていた名前を、agentcore で供給する。

移植の継ぎ目はここ 1 か所に閉じている。本体（`toolloop` / `statemachine`）は
agent_loop の断片の**逐語コピー**で、ここが同じ綴りの名前を用意することで
「借りている前提」だけを差し替える。だから AST の突き合わせが成立する。
"""
from __future__ import annotations

import os
from pathlib import Path

# agent_loop/_head.py の同名定数（逐語）。
AGENT_HOME = ".agents"


def agent_home_subdir(env_var: str, *parts: str) -> Path:
    """共通ホーム配下の状態ディレクトリ（`$<env_var>` があればそれを最優先）。"""
    override = os.environ.get(env_var)
    if override:
        return Path(os.path.expanduser(override))
    return Path.home().joinpath(AGENT_HOME, *parts)


def import_agentcli():
    """agent_loop 版は sys.path を継ぎ足して探すが、ここは agentcore の中なので直に import。"""
    from agentcore import agentcli
    return agentcli


def _noop_budget_record(seconds: float, ref: str = "", agent_cli: str = "routine",
                        model: str = "", tokens_in=None, tokens_out=None, usd=None,
                        extra: "dict | None" = None, purpose: str = "") -> None:
    """既定の記帳＝何もしない。

    agent_loop 版は自分の台帳（`_node_budget_dir()/ledger`）へ追記するが、その置き場と
    workload 名は agent-loop 固有の状態である。移植先が黙って同じ台帳へ書くと、
    「誰が書いた行か」が実行経路をまたいで曖昧になる。書きたい host は
    :func:`set_hooks` で自分の記帳を差し込む。
    """


def _no_control_policy(purpose: str = "") -> "dict | None":
    """既定の control 解決＝無し（None）。

    None は「selection_policy が無い」と同義で、本体はそのとき従来どおり pin / 既定候補で
    走る（agent_loop 版が version < 2 の control に対して返すのと同じ値）。
    """
    return None


# host が差し込めるフック。既定は上の 2 つ。
node_budget_record = _noop_budget_record
control_policy_decision = _no_control_policy


def set_hooks(*, node_budget_record=None, control_policy_decision=None) -> None:
    """記帳と control 解決を host のものへ差し替える（省略した方は据え置き）。

    `agent-herd harness` は差し込まない（台帳も control も持たない単独実行）。
    将来 agent-loop をこの移植へ寄せるときは、agent-loop 側の
    `_node_budget_record` / `_control_policy_decision` をここへ渡せば挙動が揃う。
    """
    module = globals()
    if node_budget_record is not None:
        module["node_budget_record"] = node_budget_record
    if control_policy_decision is not None:
        module["control_policy_decision"] = control_policy_decision
