'use strict';

// 「設定 > 保存データ」: アプリが作ったファイルを種類ごとに数え、選んだ種類だけ消す。
// 数えるのも消すのも main（cleanup.js）で、ここは並べて選ばせるだけ。
(function initStorage() {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  // scan … cleanup:scan の結果（開くまで null）。busy … 削除中。error … 数えられなかった理由
  const state = { scan: null, busy: false, error: '' };

  function cleanupChecked() {
    const out = [];
    for (const box of document.querySelectorAll('[data-cleanup-key]')) if (box.checked) out.push(box.dataset.cleanupKey);
    return out;
  }

  function renderCleanupTotal() {
    const items = (state.scan && state.scan.items) || [];
    const keys = new Set(cleanupChecked());
    const total = items.filter((item) => keys.has(item.key)).reduce((n, item) => n + item.bytes, 0);
    $('cleanup-total').textContent = state.scan ? `合計 ${Fmt.bytes(total)}` : '';
    $('cleanup-run').disabled = !state.scan || state.busy || !total;
  }

  function renderCleanup() {
    const box = $('cleanup-items');
    const scanning = !state.scan;
    $('cleanup-rescan').disabled = scanning || !!state.busy;
    if (scanning) {
      box.replaceChildren(el('div', 'sub', state.error || '容量を確認中…'));
      renderCleanupTotal();
      return;
    }
    box.replaceChildren(...state.scan.items.map((item) => {
      const row = el('label', 'setting-check');
      const check = el('input');
      check.type = 'checkbox';
      check.dataset.cleanupKey = item.key;
      check.checked = item.defaultOn && item.bytes > 0;
      check.disabled = !item.bytes;
      check.onchange = renderCleanupTotal;
      const text = el('span');
      text.append(el('strong', '', item.title), el('small', '', item.detail));
      row.append(check, text, el('span', 'status', item.bytes ? Fmt.bytes(item.bytes) : 'なし'));
      return row;
    }));
    renderCleanupTotal();
  }

  async function scanCleanup() {
    state.scan = null;
    state.error = '';
    renderCleanup();
    $('cleanup-status').textContent = '';
    try {
      state.scan = await api.cleanup.scan();
      $('cleanup-status').textContent = `確認日時：${Fmt.checkedAt(state.scan.scannedAt)}`;
    } catch (error) {
      state.error = error.message;
    }
    renderCleanup();
  }

  async function runCleanup() {
    const keys = cleanupChecked();
    if (!keys.length) return;
    state.busy = true;
    $('cleanup-status').textContent = '削除中…';
    renderCleanup();
    try {
      const result = await api.cleanup.remove(keys);
      state.scan = result.scan;
      $('cleanup-status').textContent = result.failed
        ? `${Fmt.bytes(result.freed)} を削除（${result.failed} 件は削除できませんでした）`
        : `${Fmt.bytes(result.freed)} を削除しました`;
    } catch (error) {
      $('cleanup-status').textContent = '';
      $('settings-error').textContent = error.message;
      $('settings-error').hidden = false;
    } finally {
      state.busy = false;
      renderCleanup();
    }
  }

  // 設定ダイアログを開くたびに数え直す（前に開いたときの数は出さない）
  function reset() {
    state.scan = null;
    state.busy = false;
    state.error = '';
    $('cleanup-status').textContent = '';
  }

  // 「保存データ」のタブを開いたとき。まだ数えていなければ数える
  function open() {
    if (!state.scan) scanCleanup();
  }

  function init() {
    $('cleanup-rescan').onclick = scanCleanup;
    $('cleanup-run').onclick = runCleanup;
  }

  window.Storage = { init, open, reset };
})();
