'use strict';

module.exports = {
  routines: {
    // capture-pane ポーリング間隔（renderer）。0 で停止。
    captureSec: 2,
    // agent-loop send のスタンドアロン tmux セッション名を拾う。
    sessionPrefix: 'kiro',
  },
};
