'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// タブ: 概要
// ---------------------------------------------------------------------------

const STATUS_ORDER = ['proposed', 'ready', 'doing', 'offloaded', 'review', 'blocked', 'inbox', 'draft'];

// 初版（charter.md）に後からバージョン名を付けて charters/<名前>.md へ移す（昇格）。
// 既存タスクの帰属タグ・project.json の収束状態（承認済み等）・milestone カードも引き継ぐ。
async function openPromoteCharter() {
  const p = state.project;
  if (!p || !p.charter) return toast('初版の憲章（charter.md）がありません');
  $('nc-title').textContent = '初版にバージョン名を付ける';
  $('nc-desc').textContent =
    '初版の憲章に名前を付けて、計画バージョンの一覧に加えます（内容は変わりません）。' +
    '初版のタスクや承認状態も引き継がれ、他のバージョンと並行して進むようになります。';
  $('nc-name').value = '';
  $('dlg-new-charter').dataset.mode = 'promote';
  $('dlg-new-charter').showModal();
  $('nc-name').focus();
}

async function submitPromoteCharter(name) {
  const p = state.project;
  if (!p) return;
  const res = await guard('バージョン名を付ける', () => api.promoteCharter(p.dir, name));
  if (!res) return;
  uiLog('promoteCharter', res);
  toast(`初版をバージョン「${res.name}」にしました（タスク ${res.tagged} 件を引き継ぎ）`, true);
  gitPushAfterWrite(`agent-dashboard: promote charter.md to charters/${res.name}.md`, p.dir);
  await reloadProject();
}

// 稼働操作（起動 / pause / resume / stop）。pause/resume/stop は commands/ ドロップ
// （＋git push）で届き、リモート本体（WSL・別ホスト）の watch が同期間隔内に取り込む。
// 起動だけはドロップでは届かない（停止中の本体は commands/ を読めない）ため、
// この PC の CLI で `agent-project start` を実行する（startAgentProject）。
// 複数 PC 分散運用のノード別生存一覧（案6）。status/<node>.json 由来の p.nodes を表示する。
// どのノード（PC）が生きているか・応答なし（heartbeat 途絶）かを一目で見せる。ノードが
// 無い（無名エンジン・単一 PC）ときは何も出さない＝従来の見た目を変えない。純関数。
function nodesSummaryHtml(nodes) {
  const list = Array.isArray(nodes) ? nodes : [];
  if (!list.length) return '';
  const rows = list
    .map((n) => {
      const alive = n.running ? 'ok' : 'stale';
      const dot = n.running ? '🟢' : '🔴';
      const age =
        n.ageSec == null
          ? ''
          : n.running
          ? '稼働中'
          : `応答なし（最終確認 ${typeof humanizeAge === 'function' ? humanizeAge(n.ageSec * 1000) : `${Math.round(n.ageSec)}秒前`}）`;
      const host = n.host ? ` <span class="muted">@${esc(n.host)}</span>` : '';
      return `<li class="node-row node-${alive}">${dot} <b>${esc(n.node)}</b>${host} <span class="muted">${esc(age)}</span></li>`;
    })
    .join('');
  return `<section class="summary-card nodes-card" aria-label="実行ノード一覧">
    <h2 class="summary-kicker">実行ノード（PC）</h2>
    <ul class="nodes-list">${rows}</ul>
    <p class="muted">応答なしのノードに割り当てたタスクは、担当を付け替えるか要対応から再実行できます。</p>
  </section>`;
}

function lifecycleCardHtml(p) {
  const live = p.liveness || {};
  const paused = !!live.paused;
  if (!live.running) {
    // 本体が停止中: pause/stop を出しても届かない（誰も読まない）。起動だけを出す
    return `
    <div class="card full">
      <h3>自動実行</h3>
      <div class="row">
        <button class="chip primary-inline" data-start-kiro
          title="このPCで自動実行を開始します">自動実行を開始</button>
        <span class="muted">停止中です。開始するまでタスクは進みません。</span>
      </div>
    </div>`;
  }
  return `
    <div class="card full">
      <h3>自動実行</h3>
      <div class="row">
        ${
          paused
            ? '<button class="chip" data-lifecycle="resume" title="一時停止を解除して作業を再開します">再開</button>'
            : '<button class="chip" data-lifecycle="pause" title="タスクの実行を一時停止します">一時停止</button>'
        }
        <button class="chip danger" data-lifecycle="stop"
          title="自動実行を停止します">停止</button>
      </div>
      ${paused ? '<div class="muted" style="margin-top:4px">一時停止中です。再開まで作業は進みません。</div>' : ''}
    </div>`;
}

// 実行中アクションの再入ガード。ボタン連打・IPC 応答待ち中の再クリックで同じ操作
// （start の二重起動・resubmit の二重積み直し）が並走するのを防ぐ。
const _inflightActions = new Set();
async function withActionLock(key, fn) {
  if (_inflightActions.has(key)) return null;
  _inflightActions.add(key);
  try {
    return await fn();
  } finally {
    _inflightActions.delete(key);
  }
}

// 本体（agent-project）の起動。確認 → CLI 実行 → 結果を平易に伝える。
// 本体が別マシンの構成では「この PC が実行役になる」ことを事前に言い、
// CLI が無ければ人が本体マシンで打つコマンドをそのまま見せる。
async function startAgentProject() {
  return withActionLock('start-project', _startAgentProject);
}

async function _startAgentProject() {
  const p = state.project;
  if (!p) return;
  const yes = await confirmDialog(
    `${p.name}: この PC で本体（agent-project の常駐）を起動します。\n` +
      '以後この PC がタスクを実行します。\n' +
      'プロジェクトの本体が別のマシン（WSL・別 PC）にある場合は、そちらで\n' +
      '  agent-project start\nを実行するほうが適切です。\nこの PC で起動しますか？'
  );
  if (!yes) return;
  try {
    const res = await api.startProject(p.dir);
    uiLog('start', res);
    toast('本体を起動しました（タスクの消化が始まります。表示への反映まで少し時間がかかります）', true);
  } catch (err) {
    uiLog('start failed', String(err.message || err));
    await confirmDialog(
      'この PC からは起動できませんでした（agent-project CLI が見つからないか失敗）。\n' +
        `理由: ${String(err.message || err).slice(0, 200)}\n\n` +
        '本体のマシンで次のコマンドを実行してください（このプロジェクトのフォルダで）:\n' +
        '  agent-project start\n\n' +
        'CLI の場所は ⚙ 設定の「agent-project CLI」でも指定できます。'
    );
    return;
  }
  await refreshAll();
}

const LIFECYCLE_CONFIRMS = {
  pause: (p) => `${p.name}: watch の消化を一時停止します（idle 監視・指示の取り込みは継続）。よろしいですか？`,
  resume: (p) => `${p.name}: 一時停止を解除して消化を再開します。よろしいですか？`,
  stop: (p) =>
    `${p.name}: 本体プロセスを停止します。\n再開はプロジェクトのマシン（WSL 等）で agent-project start を実行してください。よろしいですか？`,
};

function bindLifecycleButtons(root) {
  for (const b of root.querySelectorAll('button[data-start-kiro]')) {
    b.addEventListener('click', () => startAgentProject());
  }
  for (const b of root.querySelectorAll('button[data-lifecycle]')) {
    b.addEventListener('click', async () => {
      const p = state.project;
      if (!p) return;
      const action = b.dataset.lifecycle;
      const yes = await confirmDialog(LIFECYCLE_CONFIRMS[action](p));
      if (!yes) return;
      const labels = { pause: '一時停止を依頼しました', resume: '再開を依頼しました', stop: '停止を依頼しました' };
      const ok = await guard('稼働操作', async () => {
        const res = await api.requestLifecycle(p.dir, action, 'agent-dashboard から操作');
        uiLog('lifecycle', action, res);
        toast(`${labels[action] || '操作を送信しました'}（反映まで少し時間がかかることがあります）`, true);
        return true;
      });
      if (ok) {
        gitPushAfterWrite(`agent-dashboard: ${action}`, p.dir);
        await reloadProject();
      }
    });
  }
}

// 概要は「現在 → 人の対応 → 進捗 → 成果」の順に読むためのハブとする。
// 機械状態の細目は詳細タブへ送り、ここでは全体像と次の一手だけを返す。
function overviewSummary(p, flowRuns) {
  const live = p.liveness || { running: false };
  const undecided = (p.needs || []).filter((n) => !n.decided);
  const byStatus = p.byStatus || {};
  const working = Math.max((byStatus.doing || 0) + (byStatus.offloaded || 0), (p.claims || []).length);
  const waiting = (byStatus.ready || 0) + (byStatus.inbox || 0) + (byStatus.draft || 0) + (byStatus.proposed || 0);
  const done = (p.archive || []).length;
  const total = done + (p.backlog || []).filter((t) => t.status !== 'rejected').length;
  const progress = total ? Math.round((done / total) * 100) : 0;
  const activeRuns = (flowRuns || []).filter((r) => !['done', 'failed', 'canceled'].includes(String(r.status))).length;

  let headline;
  let tone;
  if (live.paused) {
    headline = '作業を一時停止しています';
    tone = 'warn';
  } else if (undecided.length) {
    headline = `${undecided.length} 件の確認を待っています`;
    tone = 'action';
  } else if (!live.running) {
    headline = '自動実行は停止しています';
    tone = 'warn';
  } else if (working) {
    headline = `${working} 件のタスクを進めています`;
    tone = 'running';
  } else if (waiting) {
    headline = `次の ${waiting} 件を順番に進めます`;
    tone = 'running';
  } else {
    headline = '現在の作業は完了しています';
    tone = 'ok';
  }

  return { live, undecided, working, waiting, done, total, progress, activeRuns, headline, tone };
}

function overviewGoal(p) {
  if (p.charter && (p.charter.goal || p.charter.name)) return p.charter.goal || p.charter.name;
  const current = (p.charters || []).find((c) => c.goal) || (p.charters || [])[0];
  return current ? current.goal || current.name : '目標はまだ設定されていません';
}

function versionUsage(p, name) {
  const belongsTo = (task) => String((task.extra && task.extra.charter) || '').trim() === name;
  return {
    active: (p.backlog || []).filter(belongsTo).length,
    done: (p.archive || []).filter(belongsTo).length,
  };
}

function overviewVersionsHtml(p) {
  const versions = p.charters || [];
  const stateByVersion = (p.projectState && p.projectState.charters) || {};
  const cards = versions.map((ch) => {
    const usage = versionUsage(p, ch.name);
    const used = usage.active + usage.done;
    const versionState = stateByVersion[ch.name] || {};
    const status = versionState.status
      ? statusLabel(versionState.status)
      : usage.active
        ? '進行中'
        : usage.done
          ? '作業完了'
          : '未開始';
    const deleteHelp = used
      ? `<p class="version-delete-note">関連する作業が ${used} 件あるため削除できません。</p>`
      : '';
    return `<article class="overview-version-card">
      <div class="version-card-heading">
        <div>
          <h3>${esc(ch.name)}</h3>
          <span class="version-state">${esc(status)}</span>
        </div>
      </div>
      <div class="version-card-goal">${
        ch.goal
          ? proseHtml(ch.goal)
          : p.charter && p.charter.master && p.charter.goal
            ? `${proseHtml(p.charter.goal)}<div class="muted">（共通設定の目標を継承）</div>`
            : '<span class="muted">やることは未設定です。</span>'
      }</div>
      <div class="version-card-counts" aria-label="作業状況">
        <span><strong>${usage.active}</strong> 進行中</span>
        <span><strong>${usage.done}</strong> 完了</span>
      </div>
      ${deleteHelp}
      <div class="version-card-actions">
        <button class="summary-link secondary" data-version-edit="${esc(ch.name)}">編集</button>
        <button class="danger" data-version-delete="${esc(ch.name)}"${used ? ' disabled' : ''}>削除</button>
      </div>
    </article>`;
  }).join('');
  const canPromote = p.charter && !p.charter.master && versions.length;
  return `<section class="overview-version-section" aria-labelledby="overview-versions-title">
    <div class="overview-version-heading">
      <div>
        <h2 id="overview-versions-title">計画バージョン</h2>
        <p>目的ごとの計画と進み具合を管理します。</p>
      </div>
      <div class="summary-actions">
        ${canPromote ? '<button class="summary-link secondary" id="btn-overview-promote-version">初版に名前を付ける</button>' : ''}
        <button class="summary-link" id="btn-overview-add-version">バージョンを追加</button>
      </div>
    </div>
    ${cards ? `<div class="overview-version-grid">${cards}</div>` : '<div class="overview-version-empty">計画バージョンはまだありません。最初のバージョンを追加してください。</div>'}
  </section>`;
}

async function deleteOverviewVersion(name) {
  const p = state.project;
  if (!p) return;
  const yes = await confirmDialog(
    `計画バージョン「${name}」を削除します。\nこの操作は元に戻せません。よろしいですか？`
  );
  if (!yes) return;
  const res = await guard('計画バージョンの削除', () => api.deleteCharter(p.dir, name));
  if (!res) return;
  toast(`計画バージョン「${name}」を削除しました`, true);
  gitPushAfterWrite(`agent-dashboard: delete charters/${name}.md`, p.dir);
  await reloadProject();
}

function renderOverview() {
  const p = state.project;
  const el = $('tab-overview');
  if (!p) {
    el.innerHTML = '<div class="empty">左の一覧からプロジェクトを選択してください</div>';
    return;
  }

  const s = overviewSummary(p, state.flowRuns);
  const goalText = overviewGoal(p);
  const deliveryRows = (p.delivery || [])
    .slice(-3)
    .reverse()
    .map((cells) => `<tr>${cells.map((c) => `<td>${linkify(c)}</td>`).join('')}</tr>`)
    .join('');
  // 役割 viewer は本体（エンジン）を動かさない＝この PC での起動ボタンは出さない（案4）。
  // engineer 専用の起動をビュアー機で押すと、CLI 不在や WSL パスで失敗する（誤設定の元）。
  const isViewer = state.config && state.config.role === 'viewer';
  const lifecycle = s.live.running
    ? s.live.paused
      ? '<button class="summary-link" data-lifecycle="resume">再開</button>'
      : '<button class="summary-link secondary" data-lifecycle="pause">一時停止</button>'
    : isViewer
      ? '<span class="muted">停止中（この PC は閲覧専用。実行ノードで開始してください）</span>'
      : '<button class="summary-link" data-start-kiro>自動実行を開始</button>';

  el.innerHTML = `
    <div class="overview-shell">
      <section class="summary-hero tone-${esc(s.tone)}" aria-labelledby="summary-now-title">
        <h2 class="summary-kicker" id="summary-now-title">現在の状態</h2>
        <div class="summary-hero-main">
          <div>
            <div class="summary-headline">${esc(s.headline)}</div>
            <div class="summary-goal">${proseHtml(goalText)}</div>
          </div>
          <div class="summary-actions">${lifecycle}</div>
        </div>
        <div class="summary-progress" aria-label="全体進捗 ${s.progress}%">
          <div style="width:${s.progress}%"></div>
        </div>
        <div class="summary-progress-label">${s.total ? `${s.done} / ${s.total} 件完了（${s.progress}%）` : 'タスクはまだありません'}</div>
      </section>

      ${nodesSummaryHtml(p.nodes)}
      <div class="overview-grid">
        <section class="summary-card action-card ${s.undecided.length ? 'has-action' : ''}" aria-labelledby="summary-action-title">
          <h2 class="summary-kicker" id="summary-action-title">あなたの対応</h2>
          ${s.undecided.length
            ? `<div class="summary-number">${s.undecided.length}<span>件</span></div>
               <p>確認または判断が必要です。</p>
               <button class="summary-link" data-summary-tab="needs">対応する</button>`
            : `<div class="summary-status-ok">対応はありません</div>
               <p class="muted">このまま進行を見守れます。</p>`}
        </section>

        <section class="summary-card progress-card" aria-labelledby="summary-progress-title">
          <h2 class="summary-kicker" id="summary-progress-title">進捗</h2>
          <div class="summary-stats">
            <div><strong>${s.done}</strong><span>完了</span></div>
            <div><strong>${s.working}</strong><span>作業中</span></div>
            <div><strong>${s.waiting}</strong><span>これから</span></div>
          </div>
          <div class="summary-actions">
            <button class="summary-link" data-summary-tab="backlog">タスクを見る</button>
            <button class="summary-link secondary" data-summary-tab="flow">実行を見る${s.activeRuns ? `（${s.activeRuns}）` : ''}</button>
          </div>
        </section>

        <section class="summary-card deliveries-card" aria-labelledby="summary-deliveries-title">
          <h2 class="summary-kicker" id="summary-deliveries-title">成果</h2>
          ${deliveryRows
            ? `<div class="summary-deliveries"><table class="list">${deliveryRows}</table></div>`
            : '<p class="muted">まだ成果は記録されていません。</p>'}
          <button class="summary-link secondary" data-summary-tab="history">成果を見る</button>
        </section>
      </div>
      ${overviewVersionsHtml(p)}
    </div>`;

  for (const btn of el.querySelectorAll('button[data-summary-tab]')) {
    btn.addEventListener('click', () => switchTab(btn.dataset.summaryTab));
  }
  const addVersion = $('btn-overview-add-version');
  if (addVersion) addVersion.addEventListener('click', openAddCharterVersion);
  const promoteVersion = $('btn-overview-promote-version');
  if (promoteVersion) promoteVersion.addEventListener('click', openPromoteCharter);
  for (const btn of el.querySelectorAll('button[data-version-edit]')) {
    btn.addEventListener('click', () => openProjectFile(`charters/${btn.dataset.versionEdit}.md`));
  }
  for (const btn of el.querySelectorAll('button[data-version-delete]')) {
    btn.addEventListener('click', () => deleteOverviewVersion(btn.dataset.versionDelete));
  }
  bindLifecycleButtons(el);
}

function renderProjectSettings() {
  const el = $('tab-project-settings');
  if (!el) return;
  const p = state.project;
  if (!p) {
    el.innerHTML = '<div class="empty">プロジェクトを選択してください。</div>';
    return;
  }
  const isMaster = !!(p.charter && p.charter.master);
  const danger = p.charter
    ? `<section class="project-settings-card danger-zone" aria-labelledby="project-settings-danger-title">
        <span class="summary-kicker">危険な操作</span>
        <h3 id="project-settings-danger-title">プロジェクトのリセット</h3>
        <p class="muted">計画、タスク、履歴を消して最初からやり直します。憲章は残ります。</p>
        <button class="danger" id="btn-settings-reset">プロジェクトをリセット</button>
      </section>`
    : '';

  el.innerHTML = `<div class="project-settings-shell">
    <header class="cowork-header">
      <div>
        <span class="summary-kicker">選択中のプロジェクトに適用</span>
        <h2>プロジェクト設定</h2>
        <p class="muted">${esc(p.name)} の目的、ルール、対象リポジトリを管理します。</p>
      </div>
    </header>
    <section class="project-settings-card" aria-labelledby="project-settings-definition-title">
      <span class="summary-kicker">基本設定</span>
      <h3 id="project-settings-definition-title">プロジェクト定義</h3>
      <p class="muted">プロジェクトの目的と、作業時に守るルールを編集します。</p>
      <div class="settings-action-grid">
        <button type="button" data-edit="charter.md"><strong>${isMaster ? 'マスター憲章' : '憲章'}</strong><span>目的と完了条件</span></button>
        <button type="button" data-edit="policy.md"><strong>運用ルール</strong><span>進め方と判断基準</span></button>
        <button type="button" data-edit="rules.md"><strong>プロジェクトルール</strong><span>すべての作業で守ること</span></button>
        <button type="button" data-edit="repos.json"><strong>リポジトリ</strong><span>作業対象と書き込み範囲</span></button>
      </div>
    </section>
    <section class="project-settings-card" aria-labelledby="project-settings-technical-title">
      <span class="summary-kicker">診断</span>
      <h3 id="project-settings-technical-title">調査と高度な設定</h3>
      <p class="muted">実行ID、内部ログ、同期方式などは通常の操作には必要ありません。</p>
      <button type="button" id="btn-project-technical-info">詳細情報を開く</button>
    </section>
    ${danger}
  </div>`;

  for (const btn of el.querySelectorAll('button[data-edit]')) {
    btn.addEventListener('click', () => openProjectFile(btn.dataset.edit));
  }
  const reset = $('btn-settings-reset');
  if (reset) reset.addEventListener('click', resetProject);
  const technicalInfo = $('btn-project-technical-info');
  if (technicalInfo) technicalInfo.addEventListener('click', () => openTechnicalInfo());
}

// プロジェクトのリセット（危険操作）。charter.md 以外の全データを削除し、バスの
// agent-flow daemon を停止する。charter が残るので、稼働中の agent-project は次パスで
// charter から再分解して最初からやり直す（done の記録・needs・決定記録もすべて消える）。
async function resetProject() {
  const p = state.project;
  if (!p || !p.charter) return;
  const sharedBusNote =
    p.busSource && p.busSource !== 'project'
      ? '\n⚠ 実行基盤を他プロジェクトと共有しています: 停止は他プロジェクトの実行にも影響します。'
      : '';
  const yes = await confirmDialog(
    `${p.name}: プロジェクト憲章（charter.md）以外の全データを削除し、実行エンジンを停止します。\n` +
      `削除対象: 計画バージョン・タスク ${p.backlog.length} 件・完了記録 ${p.archive.length} 件・要対応 ${p.needs.length} 件・` +
      `実行中 ${p.claims.length} 件、および履歴・納品記録などの全ファイル。\n` +
      `ファイルはゴミ箱へ移動します（ゴミ箱の無い環境では完全削除）。${sharedBusNote}\n` +
      `憲章はプロジェクト全体の前提（マスター）として残ります。マスターは分解されないので、` +
      `リセット後は待機状態になり、計画バージョンを追加すると作業が再開します。よろしいですか？`
  );
  if (!yes) return;
  const ok = await guard('プロジェクトのリセット', async () => {
    const res = await api.resetProject(p.dir, p.workspace);
    uiLog('reset', res);
    const d = res.daemon || {};
    const daemonMsg = !d.running
      ? '実行エンジンは稼働していませんでした'
      : d.stopped
        ? '実行エンジンを停止しました'
        : d.remote
          ? '実行エンジンは別のマシンで稼働中のため、そちらで停止してください'
          : '実行エンジンを停止できませんでした';
    const errMsg = res.errors && res.errors.length ? `／削除できなかったもの ${res.errors.length} 件` : '';
    const masterMsg = res.masterized ? '／憲章をマスターに整えました' : '';
    toast(`${p.name}: ${res.removed.length} 件を削除（憲章は残しました）${masterMsg}${errMsg}。${daemonMsg}`, !errMsg);
    return true;
  });
  if (ok) {
    gitPushAfterWrite('agent-dashboard: project reset (keep charter)', p.dir);
    await reloadProject();
  }
}

function linkify(text) {
  return esc(text).replace(/(https?:\/\/[^\s)&"<>]+)/g, '<a href="#" data-ext="$1">$1</a>');
}
