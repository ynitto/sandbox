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
    // boardRepos … 委譲公示板（agent-board）のリポジトリ作業ディレクトリ。ここへ post/award/cancel を
    //   ファイルとして投函し、一覧（delegation:list）でも走査する。落札→引き渡しは各ノードの
    //   board デーモンが担う（dashboard は板へファイルを書くだけ・git 同期はしない）。
    boardRepos: [],
    // nodeCommandsDir … この端末への指示（引き受け・取りやめ・引き渡し先の確定）を置くフォルダ。
    //   空なら実行エンジンと同じ場所（`<実行エンジンのホーム>/commands`）を使う。実行エンジンが
    //   読まない場所へ置いても届かないので、既定は必ず実行エンジンのホーム解決に従わせる。
    //   明示するのは、実行エンジンのホームを自動で引けない構成のときだけ。
    nodeCommandsDir: '',
    refreshSec: 15,
  },
};
