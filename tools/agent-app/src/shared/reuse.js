'use strict';
(function expose(global) {
  const DATE_MODES = Object.freeze(Object.assign(Object.create(null), { '@date:today': '今日', '@date:yesterday': '昨日', '@date:month': '今月', '@date:previous-month': '前月' }));
  function resolveDate(value, now = new Date()) {
    if (!DATE_MODES[value]) return value;
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12);
    if (value === '@date:yesterday') d.setDate(d.getDate() - 1);
    if (value === '@date:previous-month') { d.setDate(1); d.setMonth(d.getMonth() - 1); }
    const month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    return value.endsWith('month') ? month : `${month}-${String(d.getDate()).padStart(2, '0')}`;
  }
  function resolveInputs(values, now = new Date()) {
    return Object.fromEntries(Object.entries(values || {}).map(([k, v]) => [k, resolveDate(v, now)]));
  }
  function presets(raw) {
    return (Array.isArray(raw) ? raw : []).slice(0, 20).filter(p => p && typeof p.name === 'string' && p.name.trim()).map(p => ({
      name: p.name.trim().slice(0, 60), policy: ['recommended', 'saving', 'quality', 'direct'].includes(p.policy) ? p.policy : 'recommended',
      cli: String(p.cli || '').slice(0, 80), model: String(p.model || '').slice(0, 160),
      readonly: p.readonly === true, autoApprove: p.autoApprove === true,
      skillMode: ['auto', 'manual', 'off'].includes(p.skillMode) ? p.skillMode : 'auto',
      skills: (Array.isArray(p.skills) ? p.skills : []).filter(s => typeof s === 'string').slice(0, 20),
    }));
  }
  function artifacts(text) {
    const paths = [];
    for (const line of String(text || '').split('\n')) {
      const mark = line.match(/^@artifact\s+(.+?)\s*$/);
      if (mark) paths.push(mark[1]);
    }
    for (const match of String(text || '').matchAll(/\[[^\]\n]+\]\(<?([^\s)]+)>?\)/g)) paths.push(match[1].replace(/>$/, ''));
    return [...new Set(paths)].filter(p => p && !/^(?:[a-z][a-z0-9+.-]*:|[/\\])/i.test(p) && !p.split(/[/\\]/).includes('..') && !/[\x00-\x1f]/.test(p)).slice(0, 20);
  }
  function conversation(messages) {
    const turns = (messages || []).filter(m => m && ['user', 'assistant'].includes(m.role));
    const text = turns.map(m => `${m.role === 'user' ? '利用者' : 'AI'}:\n${m.text || ''}${(m.attachments || []).length ? '\n参照ファイル: ' + m.attachments.map(a => a.rel || a.name).filter(Boolean).join(', ') : ''}${m.error ? '\nエラー: ' + m.error : ''}`).join('\n\n');
    if (text.length > 100000) throw new Error('会話が長すぎます。定型化したい範囲を短い会話にまとめてください');
    if (!text.trim()) throw new Error('定型化する会話がありません');
    return text;
  }
  function creationPrompt({ kind, purpose, repo, originRepo }) {
    const instructions = {
      skill: 'スキルを作成してください。保存先の既存のスキル配置規約に従い、規約がなければ .agents/skills/<name>/SKILL.md に保存してください。skill-creator が利用可能なら参照してください。name と具体的な発動条件を示す description を frontmatter に書き、本文に判断基準・手順・確認方法を記載してください。単発の入力や機密情報を固定しないでください。',
      task: '再実行できるタスクを作成してください。statemachine-use と既存のタスク定義・スキーマを参照し、.statemachine/<name>/ に保存してください。可変入力、手順、分岐、完了条件を明確にしてください。',
      workflow: '複数担当のワークフローを作成してください。agent-flow と既存のワークフロー定義・スキーマを参照し、.agents/workflows/<name>.json に保存してください。各担当の役割、依存関係、受け渡す成果物、独立レビューや統合の条件を明確にしてください。',
    };
    if (!Object.hasOwn(instructions, kind) || !purpose?.trim() || purpose.length > 30000 || !repo) throw new Error('種類・保存先・作成する内容を確認してください');
    return `${instructions[kind]}
ユーザーが選んだ保存先: ${repo}
元の会話のリポジトリ: ${originRepo}
この新規セッションで作成してください。元の会話へのアクセスを前提にせず、以下の依頼を使ってください。相対パスは元リポジトリ由来の可能性があるため、保存先で存在を確認してください。
保存先のリポジトリ規約を最初に確認してください。必要な仕様が見つからなければ形式を推測せず確認してください。既存ファイルと衝突する場合は別名を使ってください。グローバルなスキルのインストールはしません。作成物を検証し、保存したパスと使い方を報告してください。定型化した仕事自体の実行は今回の依頼には含みません。
<creation-request>
${purpose.trim()}
</creation-request>`;
  }
  const api = { DATE_MODES, resolveDate, resolveInputs, presets, artifacts, conversation, creationPrompt };
  if (typeof module !== 'undefined') module.exports = api;
  else global.Reuse = api;
}(typeof window === 'undefined' ? globalThis : window));
