'use strict';

module.exports = {
  agentKnowledge: {
    // quiet 運転の返信下書き承認キュー（moltbook-use の moltbook_drafts.py）を
    // 呼ぶ起動コマンド。空なら moltbook-use 未導入として扱い、承認キューを出さない
    // （記憶 3 層 + moltbook の集計は agent-audit の設定だけで動くので、この空欄は
    // 承認キューだけを落とす）。
    // 例: python3 ~/repo/.github/skills/moltbook-use/scripts/moltbook_drafts.py
    draftsCommand: '',
    // WSL ディストロ名。空なら既定ディストロ（Windows でのみ意味を持つ）
    distro: '',
    // --drafts-dir で渡す（空なら moltbook_drafts.py 側の既定
    // {agent_home}/.moltbook/outbox/drafts）
    draftsDir: '',
  },
};
