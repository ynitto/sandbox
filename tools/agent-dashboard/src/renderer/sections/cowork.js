'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// 定常業務の実行履歴とログ（定常業務領域の「実行の記録」タブの中身）
// ---------------------------------------------------------------------------
//
// 以前はダイアログ（dlg-cowork-history）で見せていた。定常業務が独立した領域になり、
// 「動かす（作業）」と「動いた結果を追う（実行の記録）」が同じ領域の別タブになったので、
// 同じ内容をダイアログでも出すのはやめた——同じ話題の画面を 2 か所に置かない。

function fmtLogSize(bytes) {
  const n = Number(bytes) || 0;
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

// 実行の記録を読み込む（選択中業務の右ペインが描く）。
async function loadCoworkHistory(id, name) {
  if (!api.coworkItemLogs) return;
  state.coworkHistory = { id, name, loading: true };
  renderCowork();
  const res = await state.coworkHistoryCache.load(
    id,
    () => guard('実行の記録の読込', () => api.coworkItemLogs(id))
  );
  if (!state.coworkHistory || state.coworkHistory.id !== id) return; // 切替済み
  if (!res) {
    state.coworkHistory = { id, name, error: true };
    renderCowork();
    return;
  }
  state.coworkHistory = { id, name: res.name || name, ...res, file: '', text: '' };
  renderCowork();
  // 最新ログがあれば自動で開く（記録タブを開いた人が最初に見たいのは直近の実行）
  const first = (res.logs || [])[0];
  if (first) await selectCoworkLog(first.file);
}

// 選択中業務を維持したまま、右ペインの履歴を読み込む。
async function openCoworkHistory(id, name) {
  await loadCoworkHistory(id, name);
}

function coworkHistoryForSelected(id) {
  const history = state.coworkHistory;
  return history && String(history.id) === String(id) ? history : null;
}

// 記録の本体（実行履歴の表 + ログ一覧 + ログ本文）。置き場所を問わない純粋な組み立て。
function coworkHistoryBodyHtml(m) {
  if (!m) {
    return '<div class="empty compact">左の作業を選ぶと、その実行の記録を表示します。</div>';
  }
  if (m.loading) return '<p class="muted">読み込んでいます…</p>';
  if (m.error) return '<p class="muted">実行の記録を読み込めませんでした。</p>';
  const historyRows = (m.history || []).map((h) => `
    <tr>
      <td class="mono">${esc(fmtTime(h.at))}</td>
      <td><span class="status-chip ${h.ok ? 'st-done' : 'st-failed'}">${h.ok ? '成功' : '失敗'}</span></td>
      <td class="cowork-history-msg">${esc(h.message || '')}</td>
    </tr>`).join('');
  const logRows = (m.logs || []).map((l) => `
    <li>
      <button class="cowork-log-file ${m.file === l.file ? 'selected' : ''}" data-cowork-log="${esc(l.file)}" title="${esc(l.file)}">
        <time datetime="${esc(new Date(l.mtimeMs).toISOString())}">${esc(fmtTime(new Date(l.mtimeMs).toISOString()))}</time>
        <span class="muted">${esc(fmtLogSize(l.size))}</span>
      </button>
    </li>`).join('');
  return `
    <section class="cowork-history-runs">
      <h3>実行履歴</h3>
      ${historyRows
        ? `<div class="cowork-history-table-wrap"><table class="list cowork-history-table"><colgroup><col class="cowork-history-at"><col class="cowork-history-result"><col></colgroup><tr><th>日時</th><th>結果</th><th>メッセージ</th></tr>${historyRows}</table></div>`
        : '<p class="muted">この画面からの実行記録はまだありません。</p>'}
    </section>
    <section class="cowork-history-logs">
      <h3>ログ</h3>
      ${logRows
        ? `<div class="cowork-history-log-layout">
            <ul class="cowork-log-list">${logRows}</ul>
            <div class="cowork-log-view-wrap">
              <div class="muted" id="cowork-log-meta">${m.file ? '選択したログ' : 'ログを選択してください'}</div>
              <pre id="cowork-log-view" class="mono full-output" tabindex="0">${esc(m.text || '')}</pre>
            </div>
          </div>`
        : '<p class="muted">ログファイルが見つかりません。</p>'}
    </section>`;
}

function bindCoworkHistoryBody(root) {
  root.querySelectorAll('[data-cowork-log]').forEach((btn) =>
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
  renderCowork();
}
