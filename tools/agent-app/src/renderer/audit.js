'use strict';

// 「設定 > 監査」: 集めた記録の集計と、定型化したものの共有・改善。
// **数字はここで作らない。** 集計と判定は main 経由で agent-audit（ホスト側）が出し、
// ここは並べて、押せる操作を出すだけ（storage.js と同じ作法）。
(function initAudit() {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  // status … audit:status（周期と連鎖の状態）。summary … 集計（開くまで null）
  const state = { status: null, summary: null, artifacts: null, error: '', busy: false };

  const KIND_LABEL = { skill: 'スキル', task: 'タスク', workflow: 'ワークフロー' };
  const VERDICT = { qualified: '基準を満たす', trial: '様子見', blocked: '使わない', unknown: '未測定' };

  function tokens(n) {
    const value = Number(n) || 0;
    if (value >= 1000000000) return `${(value / 1000000000).toFixed(1)}B`;
    if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
    if (value >= 1000) return `${Math.round(value / 1000)}k`;
    return String(value);
  }

  function line(label, value) {
    const row = el('div', 'row');
    row.append(el('span', '', label), el('span', 'spacer'), el('small', 'sub', value));
    return row;
  }

  // 連鎖の状態と、足りないものの 1 行。
  function renderStatus() {
    const s = state.status;
    const box = $('audit-status');
    $('audit-run').disabled = !s || s.running || state.busy;
    if (!s) { box.textContent = ''; return; }
    const parts = [];
    if (s.running) parts.push(`${s.step || '集めています'}…`);
    else if (s.available === false) parts.push('agent-audit が見つかりません（agent-tools の install.sh で入ります）');
    else if (s.lastError) parts.push(s.lastError);
    else if (!s.lastRunAt) parts.push('まだ集めていません');
    else parts.push(`${Fmt.checkedAt(s.lastRunAt)} に集めました`);
    if (!s.running && s.artifacts && s.artifacts.count) parts.push(`定型化したもの ${s.artifacts.count} 件`);
    box.textContent = parts.join(' · ');
  }

  function renderUsage() {
    const box = $('audit-usage');
    if (state.error) { box.replaceChildren(el('div', 'sub', state.error)); return; }
    if (!state.summary) { box.replaceChildren(el('div', 'sub', '集計しています…')); return; }
    if (state.summary.available === false) {
      box.replaceChildren(el('div', 'sub', 'agent-audit を入れると使えます'));
      return;
    }
    const rows = ((state.summary.usage || {}).rows) || [];
    const out = [];
    for (const row of rows) {
      const measured = (row.measured_in || 0) + (row.measured_out || 0);
      const detail = measured
        ? `${row.runs} 回 · ${tokens(row.measured_in)} / ${tokens(row.measured_out)}`
        : `${row.runs} 回 · 実測なし`;
      out.push(line(row.group || '(なし)', detail));
    }
    const led = (state.summary.quality || {}).ledger;
    if (led && led.runs) {
      const failed = led.runs - (led.status.done || 0);
      out.push(line('成功', `${Math.round((led.pass_rate || 0) * 100)}%${failed ? `（失敗 ${failed} 件）` : ''}`));
    }
    if (!out.length) out.push(el('div', 'sub', 'まだ記録がありません'));
    box.replaceChildren(...out);
  }

  // 成果物 1 件 1 行。基準を割ったものにだけ「改善案を出す」を出す（押せる操作だけ並べる）。
  function renderArtifacts() {
    const box = $('audit-artifacts');
    const doc = state.artifacts;
    if (!doc) { box.replaceChildren(el('div', 'sub', '確認しています…')); return; }
    const shared = new Map((doc.share || []).map((item) => [`${item.kind}/${item.name}`, item]));
    const items = doc.items || [];
    if (!items.length) {
      box.replaceChildren(el('div', 'sub', '会話から定型化したものが、初めて成功するとここに並びます'));
      return;
    }
    box.replaceChildren(...items.map((item) => {
      const row = el('div', 'row');
      const share = shared.get(`${item.kind}/${item.name}`) || {};
      // 右端は判定だけを短く出し、回数と共有の状態は補助の文字にする（長いチップにしない）
      const notes = [];
      if (item.samples) notes.push(`${item.samples} 回中 ${item.passed || 0} 成功`);
      if (share.improveBranch && !share.improveMergedAt) notes.push('改善案を出しました');
      else if (share.submittedBranch) notes.push('共有しました');
      // 判定とボタンは右端に置く（行ごとに位置が動かないよう、回数は名前側へ寄せる）
      row.append(
        el('span', '', item.name),
        el('small', 'sub', [KIND_LABEL[item.kind] || item.kind, ...notes].join(' · ')),
        el('span', 'spacer'),
        el('span', 'status', VERDICT[item.status] || item.status),
      );
      if ((item.status === 'trial' || item.status === 'blocked')
          && !(share.improveBranch && !share.improveMergedAt)) {
        const button = el('button', 'small', '改善案を出す');
        button.type = 'button';
        button.onclick = () => improve(item, button);
        row.append(button);
      }
      return row;
    }));
  }

  function render() {
    renderStatus();
    renderUsage();
    renderArtifacts();
  }

  async function loadSummary() {
    state.summary = null;
    state.error = '';
    renderUsage();
    try {
      state.summary = await api.audit.summary({ by: $('audit-by').value, period: 'month' });
    } catch (error) {
      state.error = error.message;
    }
    renderUsage();
  }

  async function loadArtifacts() {
    try { state.artifacts = await api.audit.artifacts(); } catch { state.artifacts = { items: [] }; }
    renderArtifacts();
  }

  async function run() {
    state.busy = true;
    renderStatus();
    try {
      await api.audit.run();
      await Promise.all([loadSummary(), loadArtifacts()]);
    } catch (error) {
      $('settings-error').textContent = error.message;
      $('settings-error').hidden = false;
    } finally {
      state.busy = false;
      renderStatus();
    }
  }

  async function improve(item, button) {
    button.disabled = true;
    button.textContent = '出しています…';
    try {
      const result = await api.audit.improve({
        origin: item.origin, kind: item.kind, name: item.name,
        evidence: (item.failure_modes || []).map((mode) => ({ status: 'failed', error_class: mode })),
      });
      if (result.skipped) {
        $('settings-status').textContent = result.skipped === 'no-share-repo'
          ? '共有先を入れてください' : `出せませんでした（${result.error || result.skipped}）`;
      } else {
        $('settings-status').textContent = `${result.branch} を出しました`;
      }
      await loadArtifacts();
    } catch (error) {
      $('settings-error').textContent = error.message;
      $('settings-error').hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = '改善案を出す';
    }
  }

  // 設定の値を画面へ。共有先が空なら「main へ直接」は出さない（要るまで出さない）。
  function fill(config) {
    const cfg = (config && config.audit) || {};
    $('audit-enabled').checked = cfg.enabled !== false;
    $('audit-interval').value = String([0, 30, 60, 360, 1440].includes(cfg.intervalMinutes) ? cfg.intervalMinutes : 60);
    $('audit-share-repo').value = cfg.shareRepo || '';
    $('audit-push-main').checked = !!cfg.pushToMain;
    renderShareRepoRow();
  }

  function renderShareRepoRow() {
    $('audit-push-main-row').hidden = !$('audit-share-repo').value.trim();
  }

  function patch() {
    return {
      enabled: $('audit-enabled').checked,
      intervalMinutes: Number($('audit-interval').value),
      shareRepo: $('audit-share-repo').value.trim(),
      pushToMain: $('audit-push-main').checked,
    };
  }

  // 設定ダイアログを開くたびに数え直す（前に開いたときの数は出さない）
  function reset() {
    state.summary = null;
    state.artifacts = null;
    state.error = '';
    state.busy = false;
  }

  // 「監査」のタブを開いたとき
  function open() {
    if (!state.status) api.audit.status().then((got) => { state.status = got; renderStatus(); }).catch(() => {});
    if (!state.summary) loadSummary();
    if (!state.artifacts) loadArtifacts();
  }

  function init() {
    $('audit-run').onclick = run;
    $('audit-by').onchange = loadSummary;
    $('audit-share-repo').oninput = renderShareRepoRow;
    api.audit.onChanged((got) => { state.status = got; renderStatus(); });
  }

  window.Audit = { init, open, reset, fill, patch, render };
})();
