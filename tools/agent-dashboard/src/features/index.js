'use strict';

// ダッシュボードに載せる制御面の一覧。
//
// - base … Electron シェル・git・GitLab・共通 IPC（src/base/）
// - agent-project … agent-project / agent-flow の可視化と操作（本リポジトリが維持）
// - routines … agent-loop tmux の視聴（Phase A: capture-pane）
// - cowork … 定期実行と定型業務の管理・監視
// - amigos … agent-amigos ミッションの読み取りビューとノード予算（node-budget 契約）の管理
// - orchestration … ノード予算 v2（トークン配分）・エージェント制御（agent-control）・
//                    エージェント CLI ドロップイン（agent-cli）の横断オーケストレーション管理面
// - delegation … agent-flow / agent-amigos 間の内部連携を共通封筒（delegation 契約）で扱う。
//                 利用者の操作はミッション／要対応／実行へ集約し、独立画面は持たない
// - participation … flow / amigos の募集中の仕事へ、この端末から参加するための小さい操作面
// - agent-audit … agent-audit CLI（WSL 経由）の LLM 不使用段の呼び出しと、
//                  収集済みトークン利用量・実行品質の表示（記憶 3 層 + moltbook の
//                  健全性サマリーも同じ利用状況領域に含む。§ agent-audit 参照）
// - adhoc-flow … プロジェクトを立てない agent-flow 単発 run の投入・監視と、
//                 フロービルダー（保存済みフロー定義 → submit_request の plan）・
//                 成果のタスク昇格（S21・S22）
// - documents … 文書ルールに沿ってエージェント CLI に文書（Word / PowerPoint / Excel /
//               Markdown / draw.io）を作らせ、検証し、改訂履歴のサイドカーを残す制御面
//
// 新しい制御面を足す手順:
//   1. src/features/<id>/ を agent-project や routines を雛形に作る
//   2. この配列に require('./<id>') を追加する
//   3. 必要なら renderer のタブ／サイドバーに UI を足す
//
// フルプラグイン（動的ロード・サンドボックス）にはしない。
// ソースツリー上の分離と、上流更新時のマージ容易性を優先する。

function loadFeatures() {
  return [
    require('./agent-project'),
    require('./routines'),
    require('./cowork'),
    require('./amigos'),
    require('./orchestration'),
    require('./delegation'),
    require('./participation'),
    require('./agent-audit'),
    require('./adhoc-flow'),
    require('./documents'),
  ];
}

module.exports = { loadFeatures };
