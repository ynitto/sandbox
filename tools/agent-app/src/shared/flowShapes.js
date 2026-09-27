'use strict';
// ワークフローの「分担の形」。複数の AI にして得をする 3 つの形と、どれにも決めない「おまかせ」。
// 画面の言葉（README「タスクとワークフローの使い分け」と同じ）・agent-flow の標準パターン・
// AI と作るときに依頼へ足す 1 行を、ここ 1 か所に持つ（作成画面・依頼から実行・main が同じものを見る）。
(function expose(global) {
  const SHAPES = Object.freeze([
    Object.freeze({
      id: 'verify', label: '別の目で確かめる', pattern: 'adversarial-verification',
      title: '作る担当と確かめる担当を分け、確認が通るまで作り直す',
      example: '例: 不具合を直し、別の AI に確かめさせる',
      instruction: '分担の形は「別の目で確かめる」です。作る工程と、それを確かめる verify の工程を分けてください。verify の goal には、作る工程の説明を繰り返さず、独立に確かめる観点と合格の条件を書いてください。確認が通らなければ作る工程へ戻す rework（trigger は verification-failed）を書いてください。',
    }),
    Object.freeze({
      id: 'compare', label: '並べて比べる', pattern: 'tournament',
      title: '複数の案を出させ、決めた基準で選ぶ',
      example: '例: 設計案を 3 つ出させ、基準で 1 つ選ぶ',
      instruction: '分担の形は「並べて比べる」です。案を出す generate の工程を互いに依存させずに複数置き、それらを deps に持つ judge（1 つ選ぶ）か filter（条件で絞る）の工程で選んでください。選ぶ基準は judge / filter の goal に、確かめられる条件として書いてください。',
    }),
    Object.freeze({
      id: 'split', label: '分けて広く進める', pattern: 'fan-out-and-synthesize',
      title: '量の多い仕事を分けて同時に進め、最後にまとめる',
      example: '例: 対象のファイルを分けて調べ、結果をまとめる',
      instruction: '分担の形は「分けて広く進める」です。互いに依存しない単位に分けた工程を並列に置き、最後にそれらを deps に持つ synthesize の工程でまとめてください。分ける件数が実行時まで決まらないなら split と map を使ってください。',
    }),
  ]);
  const AUTO = Object.freeze({ id: '', label: 'おまかせ', title: '目的から形を決める' });

  function find(id) {
    return SHAPES.find((shape) => shape.id === id) || null;
  }
  // 画面や sidecar から来た値を揃える。知らない値は「おまかせ」（''）。
  function normalize(id) {
    return find(String(id || '')) ? String(id) : '';
  }

  const api = { SHAPES, AUTO, find, normalize };
  if (typeof module !== 'undefined') module.exports = api;
  else global.FlowShapes = api;
}(typeof window === 'undefined' ? globalThis : window));
