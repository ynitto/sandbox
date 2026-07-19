'use strict';

// 委譲制御面の設定既定。
// - flowBusDirs … 一覧（delegation:list）で走査する agent-flow バスの明示指定。
//   amigos は発見済みホームのバスから自動で集まるが、flow のバスはプロジェクト単位で
//   解決されるため、横断一覧では監視対象のバスをここに列挙する（renderer は選択中
//   プロジェクトの既知バスを直接渡す運用も可）。
// - refreshSec … 委譲タブの自動更新間隔（秒）。
module.exports = {
  delegation: {
    flowBusDirs: [],
    refreshSec: 15,
  },
};
