'use strict';

// ダッシュボードに載せる制御面の一覧。
//
// - base … Electron シェル・git・GitLab・共通 IPC（src/base/）
// - agent-project … agent-project / agent-flow の可視化と操作（本リポジトリが維持）
// - kiro-loop … kiro-loop tmux の視聴（Phase A: capture-pane）
// - cowork … 定期実行と定型業務の管理・監視
// - amigos … agent-amigos ミッションの読み取りビューとノード予算（node-budget 契約）の管理
// - orchestration … ノード予算 v2（トークン配分）・エージェント制御（agent-control）・
//                    エージェント CLI ドロップイン（agent-cli）の横断オーケストレーション管理面
//
// 新しい制御面を足す手順:
//   1. src/features/<id>/ を agent-project や kiro-loop を雛形に作る
//   2. この配列に require('./<id>') を追加する
//   3. 必要なら renderer のタブ／サイドバーに UI を足す
//
// フルプラグイン（動的ロード・サンドボックス）にはしない。
// ソースツリー上の分離と、上流更新時のマージ容易性を優先する。

function loadFeatures() {
  return [
    require('./agent-project'),
    require('./kiro-loop'),
    require('./cowork'),
    require('./amigos'),
    require('./orchestration'),
  ];
}

module.exports = { loadFeatures };
