'use strict';

// 「設定 > アプリ」の更新と、見つかった更新を見せるダイアログ。
(function initAppUpdate() {
  const $ = (id) => document.getElementById(id);

  // current … update:status（更新元・確認結果・適用中の進み）。届くまで null
  // dismissed … 「あとで」で閉じた更新の内容（同じ内容は次の起動まで自動では出さない）
  const state = { current: null, dismissed: '' };
  let notice = () => {};

  //
  // 確認は main（起動時・定期）と「今すぐ確認」で行い、結果は update:changed で届く。ここでは
  // 見つかった更新を 1 つのダイアログで見せ、利用者が「更新する」を押した分だけ apply へ渡す。
  // 「あとで」は同じ内容を次の起動まで出さない（手動の確認では改めて出す）。

  function updateKey(plan) {
    return plan ? `${plan.app.available ? plan.app.next : ''}|${plan.tools.available ? plan.tools.next : ''}` : '';
  }

  function renderUpdateStatus() {
    const u = state.current;
    const line = $('update-status');
    const button = $('update-check');
    if (!line) return;
    button.disabled = !!(u && (u.checking || u.applying));
    if (!u) { line.textContent = ''; return; }
    const parts = [`Agent App ${u.appVersion}`];
    if (u.checking) parts.push('確認しています…');
    else if (u.applying) parts.push(u.progress || '更新しています…');
    else if (u.error) parts.push(u.error);
    else if (!u.source) parts.push('更新元が未設定');
    else if (!u.lastCheckAt) parts.push('未確認');
    else {
      const p = u.plan;
      const found = [];
      if (p && p.app.available) found.push(`Agent App ${p.app.next}${p.app.applicable ? '' : '（この起動形態では手動で入れ替え）'}`);
      if (p && p.tools.available) found.push(`agent-tools ${p.tools.next}`);
      parts.push(found.length ? `新しい版: ${found.join(' / ')}` : '最新です');
      parts.push(`${Fmt.checkedAt(u.lastCheckAt)} 確認`);
    }
    const t = u.plan && u.plan.tools;
    if (t && t.installed && !t.available) {
      parts.push(!t.configured ? 'agent-tools: 更新元の設定なし' : t.error ? `agent-tools: ${t.error}` : t.current ? `agent-tools ${t.current}` : 'agent-tools');
    }
    line.textContent = parts.join(' · ');
  }

  function renderUpdateDialog() {
    const u = state.current || {};
    const p = u.plan;
    if (!p) return;
    const appOk = p.app.available && p.app.applicable;
    $('update-app-row').hidden = !appOk;
    $('update-app').checked = appOk;
    $('update-app-detail').textContent = appOk ? `${p.app.current} → ${p.app.next}（入れ替えのために再起動します）` : '';
    $('update-tools-row').hidden = !p.tools.available;
    $('update-tools').checked = p.tools.available;
    $('update-tools-detail').textContent = p.tools.available
      ? `${p.tools.current} → ${p.tools.next}（${api.platform === 'win32' ? 'WSL' : 'この端末'}で入れ直します）` : '';
    $('update-notes').textContent = p.notes || '';
    $('update-notes').hidden = !p.notes;
    $('update-progress').textContent = u.applying ? (u.progress || '更新しています…') : '';
    $('update-apply').disabled = !!u.applying;
    $('update-later').disabled = !!u.applying;
    $('update-close').disabled = !!u.applying;
  }

  function openUpdateDialog() {
    const dlg = $('app-update');
    $('update-error').hidden = true;
    renderUpdateDialog();
    if (!dlg.open) dlg.showModal();
  }

  async function applyUpdate() {
    const choice = { app: !$('update-app-row').hidden && $('update-app').checked, tools: !$('update-tools-row').hidden && $('update-tools').checked };
    if (!choice.app && !choice.tools) { $('app-update').close(); return; }
    $('update-error').hidden = true;
    $('update-apply').disabled = true;
    try {
      const result = await api.update.apply(choice);
      if (result.app) { $('update-progress').textContent = '入れ替えのために終了します…'; return; }
      $('app-update').close();
      if (result.tools) notice(`agent-tools を ${result.tools} に更新しました`);
    } catch (error) {
      $('update-error').textContent = error.message;
      $('update-error').hidden = false;
      $('update-apply').disabled = false;
    }
  }

  async function checkUpdateNow() {
    const button = $('update-check');
    button.disabled = true;
    try {
      const plan = await api.update.check();
      state.current = await api.update.status();
      renderUpdateStatus();
      if (plan && plan.any) { state.dismissed = ''; openUpdateDialog(); }
    } catch (error) {
      state.current = await api.update.status().catch(() => state.current);
      renderUpdateStatus();
      $('settings-error').textContent = error.message;
      $('settings-error').hidden = false;
    } finally {
      button.disabled = false;
    }
  }

  function init(options = {}) {
    if (options.notice) notice = options.notice;
    $('update-check').onclick = checkUpdateNow;
    $('update-apply').onclick = applyUpdate;
    $('update-later').onclick = () => { state.dismissed = updateKey((state.current || {}).plan); $('app-update').close(); };
    $('update-close').onclick = () => $('app-update').close();
    api.update.onChanged((u) => {
      state.current = u;
      renderUpdateStatus();
      if ($('app-update').open) renderUpdateDialog();
      // 起動時・定期の確認で見つかった分は、同じ内容を「あとで」で閉じていなければ出す
      if (u.trigger === 'auto' && u.plan && u.plan.any && !u.applying && updateKey(u.plan) !== state.dismissed) openUpdateDialog();
    });
    api.update.status().then((u) => { state.current = u; renderUpdateStatus(); }).catch(() => {});
  }

  window.AppUpdate = { init, renderStatus: renderUpdateStatus };
})();
