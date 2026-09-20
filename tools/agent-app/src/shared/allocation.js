'use strict';
(function expose(global) {
  const MODES = ['configured', 'local', 'cloud', 'local-only'];
  const LABELS = { configured: '通常の配分', local: 'ローカル優先', cloud: 'クラウド優先', 'local-only': 'クラウドを使わない' };
  function normalize(raw) {
    const s = raw && typeof raw === 'object' ? raw : {};
    const t = s.temporary;
    return {
      mode: MODES.includes(s.mode) ? s.mode : 'configured',
      localModel: String(s.localModel || '').trim().slice(0, 160),
      temporary: t && t.mode === 'local' && (t.until === null || Number.isFinite(Date.parse(t.until)))
        ? { mode: 'local', until: t.until === null ? null : new Date(t.until).toISOString() } : null,
    };
  }
  function active(raw, now = Date.now()) {
    const s = normalize(raw);
    return s.temporary && (s.temporary.until === null || Date.parse(s.temporary.until) > now) ? s.temporary : null;
  }
  function mode(raw, now = Date.now()) { return active(raw, now)?.mode || normalize(raw).mode; }
  function isLocal(cli, agents = []) {
    return ['herd', 'ollama', 'aider'].includes(cli) || agents.some(a => a.name === cli && a.command === 'agent-herd');
  }
  // Shared by the preview and execution resolver. An explicit AI always wins.
  function select(selected, config, { agents = null, now = Date.now(), preference } = {}) {
    if (['direct', 'shared'].includes(selected.policy)) return selected;
    const s = normalize(config.allocation);
    const effective = MODES.includes(preference) ? preference : mode(s, now);
    const available = cli => !agents || agents.some(a => a.name === cli && a.available);
    if (effective === 'local' || effective === 'local-only') {
      if (available('herd')) return { ...selected, cli: 'herd', model: s.localModel, allocation: effective };
      if (effective === 'local-only') throw new Error('ローカルのAIが利用できません。実行制御で配分を変更してください');
    }
    if (effective === 'cloud' && isLocal(selected.cli, agents || [])) {
      const candidate = Object.values(config.execution?.tiers || {}).find(p => !isLocal(p.cli, agents || []) && available(p.cli));
      if (candidate) return { ...selected, ...candidate, allocation: effective };
      throw new Error('クラウドのAIを実行制御で設定してください');
    }
    return selected;
  }
  function validLimit(item, now = Date.now()) {
    if (!item || (Number.isFinite(Date.parse(item.reset_at)) && Date.parse(item.reset_at) <= now)) return false;
    const raw = item.quota_used_percent;
    return raw !== null && raw !== undefined && raw !== '' && Number.isFinite(Number(raw)) && Number(raw) >= 0 && Number(raw) <= 100;
  }
  function manualLimits(raw) {
    return (Array.isArray(raw) ? raw : []).filter(x => x && /^[a-zA-Z0-9_-]{1,80}$/.test(x.agent_cli)
      && validLimit(x, 0) && Number.isFinite(Date.parse(x.reset_at)) && Number.isFinite(Date.parse(x.observed_at)))
      .slice(0, 30).map(x => ({ agent_cli: x.agent_cli, quota_used_percent: Number(x.quota_used_percent),
        reset_at: new Date(x.reset_at).toISOString(), observed_at: new Date(x.observed_at).toISOString(), quota_source: 'manual' }));
  }
  function limits(observed, manual, now = Date.now()) {
    const rows = Array.isArray(observed) ? observed : [];
    return [...rows, ...manualLimits(manual).filter(m => !rows.some(r => r.agent_cli === m.agent_cli && validLimit(r, now)))];
  }
  const api = { MODES, LABELS, normalize, active, mode, select, isLocal, validLimit, manualLimits, limits };
  if (typeof module !== 'undefined') module.exports = api;
  else global.Allocation = api;
}(typeof window === 'undefined' ? globalThis : window));
