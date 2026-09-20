'use strict';

// Selection belongs to agent-tools. The app only supplies its configured, runnable
// candidates (and agent-audit's ratings when it has them) and validates the returned
// choice before launching anything.
const allocation = require('../shared/allocation');
const herd = require('./herd');

function pending(session) {
  return session.allocation === 'auto' && !session.modelSelection
    && !(session.messages || []).some(m => ['user', 'assistant'].includes(m.role));
}

function candidates(config, agents, load, { readonly = false, attachments = [], observed = [] } = {}) {
  const limits = allocation.limits(observed, config.audit?.manualLimits);
  const blocked = new Set(limits.filter(r => allocation.validLimit(r) && Number(r.quota_used_percent) >= 100).map(r => r.agent_cli));
  const choices = [...Object.values(config.execution?.tiers || {})];
  if (agents.some(a => a.name === 'herd' && a.available)) choices.push({ cli: 'herd', model: config.allocation?.localModel || '' });
  const out = new Map();
  for (const choice of choices) {
    let cli = choice.cli;
    if (herd.isHerd(cli)) {
      try { cli = herd.resolveChat(herd.purposeOf({ readonly, workFiles: herd.hasWorkFiles(attachments) }), agents).cli; } catch { continue; }
    }
    if (!agents.some(a => a.name === cli && a.available && !a.virtual) || blocked.has(cli)) continue;
    let spec;
    try { spec = load(cli); } catch { continue; }
    const model = String(choice.model || spec.defaultModel || '').trim();
    const item = { cli, model };
    out.set(JSON.stringify(item), item);
  }
  return [...out.values()];
}

async function select({ config, agents, load, prompt, readonly, attachments, cwd, capture, signal, observed, ratings = '', workload = '' }) {
  if (signal?.aborted) throw new Error('自動選択を停止しました');
  const eligible = candidates(config, agents, load, { readonly, attachments, observed });
  if (!eligible.length) throw new Error('自動選択できるAIがありません。実行制御の候補と利用枠を確認してください');
  const args = ['select', '--purpose', readonly ? 'plan' : 'work', ...eligible.flatMap(c => ['--candidate', c.cli + (c.model ? `/${c.model}` : '')])];
  if (ratings) args.push('--ratings', ratings);
  if (workload) args.push('--workload', workload);
  const result = await capture('agent-herd', args, { cwd, input: prompt, signal, timeoutMs: 90000 });
  if (signal?.aborted) throw new Error('自動選択を停止しました');
  let value;
  try { value = JSON.parse(result?.stdout); } catch { /* Older tools and process failures are not valid selections. */ }
  if (value && value.selected === null) throw new Error('利用条件を満たすAIがありません。実行制御の候補と利用枠を確認してください');
  if (!result?.ok || !value?.selected) throw new Error('AIを自動選択できませんでした。agent-toolsを更新するか、実行制御で通常の配分に変更してください');
  const chosen = eligible.find(c => c.cli === value.selected.agent_cli && c.model === value.selected.model);
  if (!chosen || !['jev', 'judge', 'audit'].includes(value.stage)) throw new Error('自動選択の結果が候補と一致しません。実行制御を確認してください');
  return { ...chosen, stage: value.stage, rated: !!ratings };
}

function information(choice) {
  const method = { jev: 'Jev', judge: 'ローカル判定', audit: choice.rated ? '実測の格付け' : '候補条件' }[choice.stage];
  return { type: 'status', title: `自動選択：${choice.cli}${choice.model ? ` / ${choice.model}` : ''}`, status: 'success', detail: `選択方法：${method}` };
}

module.exports = { pending, candidates, select, information };
