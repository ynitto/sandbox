'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// 定常業務の実行履歴とログ
// ---------------------------------------------------------------------------

function fmtLogSize(bytes) {
  const n = Number(bytes) || 0;
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

async function openCoworkHistory(id, name) {
  const dlg = $('dlg-cowork-history');
  if (!dlg || !api.coworkItemLogs) return;
  state.coworkHistory = { id, name, loading: true };
  $('cowork-history-title').textContent = `実行履歴とログ — ${name || id}`;
  $('cowork-history-body').innerHTML = '<p class="muted">読み込んでいます…</p>';
  if (!dlg.open) dlg.showModal();
  const res = await state.coworkHistoryCache.load(
    id,
    () => guard('実行履歴の読込', () => api.coworkItemLogs(id))
  );
  if (!state.coworkHistory || state.coworkHistory.id !== id) return; // 閉じられた/切替済み
  if (!res) {
    $('cowork-history-body').innerHTML = '<p class="muted">実行履歴を読み込めませんでした。</p>';
    return;
  }
  state.coworkHistory = { id, name: res.name || name, ...res, file: '', text: '' };
  renderCoworkHistory();
  // 最新ログがあれば自動で開く
  const first = (res.logs || [])[0];
  if (first) await selectCoworkLog(first.file);
}

function renderCoworkHistory() {
  const m = state.coworkHistory;
  const body = $('cowork-history-body');
  if (!m || !body) return;
  const historyRows = (m.history || []).map((h) => `
    <tr>
      <td class="mono">${esc(fmtTime(h.at))}</td>
      <td><span class="status-chip ${h.ok ? 'st-done' : 'st-failed'}">${h.ok ? '成功' : '失敗'}</span></td>
      <td class="cowork-history-msg">${esc(h.message || '')}</td>
    </tr>`).join('');
  const logRows = (m.logs || []).map((l) => `
    <li>
      <button class="cowork-log-file ${m.file === l.file ? 'selected' : ''}" data-cowork-log="${esc(l.file)}" title="${esc(l.file)}">
        <code>${esc(l.name)}</code>
      </button>
      <span class="muted">${esc(fmtTime(new Date(l.mtimeMs).toISOString()))} ・ ${esc(fmtLogSize(l.size))}</span>
    </li>`).join('');
  body.innerHTML = `
    <section class="cowork-history-runs">
      <h3>この画面からの実行（新しい順）</h3>
      ${historyRows
        ? `<div class="cowork-history-table-wrap"><table class="list"><tr><th>日時</th><th>結果</th><th>メッセージ</th></tr>${historyRows}</table></div>`
        : '<p class="muted">この画面からの実行記録はまだありません。</p>'}
    </section>
    <section class="cowork-history-logs">
      <h3>ログファイル（リポジトリの実行ログ・新しい順）</h3>
      ${logRows
        ? `<div class="cowork-history-log-layout">
            <ul class="cowork-log-list">${logRows}</ul>
            <div class="cowork-log-view-wrap">
              <div class="muted" id="cowork-log-meta">${m.file ? esc(m.file) : 'ログを選択してください'}</div>
              <pre id="cowork-log-view" class="mono full-output" tabindex="0">${esc(m.text || '')}</pre>
            </div>
          </div>`
        : '<p class="muted">ログファイルが見つかりません（.kiro-loop/logs・.statemachine-use/logs 等）。</p>'}
    </section>`;
  body.querySelectorAll('[data-cowork-log]').forEach((btn) =>
    btn.addEventListener('click', () => selectCoworkLog(btn.dataset.coworkLog)));
}

async function selectCoworkLog(file) {
  const m = state.coworkHistory;
  if (!m || !api.coworkReadLog) return;
  const res = await guard('ログの読込', () => api.coworkReadLog(m.id, file));
  if (!state.coworkHistory || state.coworkHistory.id !== m.id) return;
  if (!res) return;
  state.coworkHistory.file = res.file;
  state.coworkHistory.text = (res.truncated ? `…（先頭 ${fmtLogSize(res.size - res.text.length)} を省略・末尾のみ表示）\n` : '') + (res.text || '（空のログ）');
  renderCoworkHistory();
}
