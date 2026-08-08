'use strict';

// ホーム（ポータル）— agent-* ファミリー全体の横断ビュー。
//
// このアプリの最初の画面。プロジェクト選択に依存せず、「いま人の判断待ちはどこに
// あるか」「どのワークロード（プロジェクト / 定常業務 / ミッション / 参加）が動いて
// いるか」を 1 枚で見せ、必要な画面へ 1 クリックで移動させる。
//
// カードは registerPortalCard の登録簿から並べる。この画面自身はどの制御面のカードが
// 載るかを知らない——agent-project も他の制御面と同じ 1 枚のカードとして並ぶ。
// 例外は「あなたの対応待ち」の横断キューで、これは discover() が全プロジェクト分の
// needsCount を既に持っているため、コアのデータだけで組める（新しい取得経路は作らない）。
//
// このファイルは他の sections/*.js と同じく**関数宣言のみ**（load 時実行を持たない）。
// タブ登録（registerFeatureTab('home', …)）とコアカードの登録は bootstrap.js の init() が行う。

// 発見結果から横断サマリーを作る純関数（テスト対象）。
// projects: discover() の projects（needsCount / running / paused / quarantined / exists / kind）。
// exists:false（この PC から届かない）は集計から外す——開けない画面へ誘導しない。
// 返り値: { totals, queue }。queue は要対応のあるプロジェクトの件数降順（同数は名前順）。
function portalHomeModel(projects) {
  const totals = { projects: 0, routines: 0, running: 0, paused: 0, quarantined: 0, needs: 0 };
  const queue = [];
  for (const p of projects || []) {
    if (!p || p.exists === false) continue;
    if (p.kind === 'routine') totals.routines += 1;
    else totals.projects += 1;
    if (p.running) totals.running += 1;
    if (p.paused) totals.paused += 1;
    if (p.quarantined) totals.quarantined += 1;
    const needs = Math.max(0, Math.floor(Number(p.needsCount) || 0));
    totals.needs += needs;
    if (needs > 0) {
      queue.push({ dir: p.dir, name: p.charterName || p.name || p.dir, needs, running: !!p.running });
    }
  }
  queue.sort((a, b) => b.needs - a.needs || String(a.name).localeCompare(String(b.name)));
  return { totals, queue };
}

// 「あなたの対応待ち」— 全プロジェクト横断の要対応キュー。
// 人がこのアプリを開いて最初に知りたいのは「自分は何をすべきか」なので、カードより先に置く。
function portalNeedsQueueHtml(model) {
  const total = model.totals.needs;
  const rows = model.queue
    .map(
      (q) => `<button type="button" class="portal-queue-row" data-portal-needs="${esc(q.dir)}"
        title="${esc(q.dir)}">
        <span class="dot ${q.running ? 'running' : ''}"></span>
        <span class="name">${esc(q.name)}</span>
        <span class="badge warn" title="要対応 ${q.needs} 件">${q.needs}</span>
        <span class="portal-queue-go">要対応を開く →</span>
      </button>`
    )
    .join('');
  return `<section class="portal-queue" aria-labelledby="portal-queue-title">
    <h3 id="portal-queue-title">あなたの対応待ち
      ${total ? `<span class="badge warn">${total}</span>` : ''}</h3>
    ${total
      ? rows
      : '<div class="empty compact">いま人の判断待ちはありません。</div>'}
  </section>`;
}

// コア（sections 所有の制御面）のカード。feature モジュール所有の面（参加・利用状況）は
// それぞれの features/*.js が自分で登録する。
function registerCorePortalCards() {
  // プロジェクト（agent-project）— 他のワークロードと同格の 1 枚。
  registerPortalCard('projects', {
    order: 10,
    html: () => {
      const projects = (state.discovery && state.discovery.projects) || [];
      const model = portalHomeModel(projects);
      const t = model.totals;
      const facts = [
        `<strong>${t.projects}</strong> 件`,
        t.running ? `稼働中 ${t.running}` : '',
        t.paused ? `一時停止 ${t.paused}` : '',
        t.quarantined ? `停止中 ${t.quarantined}` : '',
      ].filter(Boolean).join('・');
      // 「概要を開く」は agent-project のプロジェクトを選んでいるときだけ（定常業務専用
      // フォルダの選択中に押させると、隠れている概要タブへ遷移してしまう）。
      const selected = projects.find((x) => x && x.dir === state.selectedDir);
      const canOpen = !!selected && selected.isProject !== false;
      return `<div class="portal-card-heading">
          <span class="summary-kicker">まとまった開発を任せる</span>
          <h3>プロジェクト</h3>
        </div>
        <p class="portal-card-count">${facts}</p>
        <p class="muted">目標（charter）からタスクへ分解し、検証と検収で完成させます。</p>
        <div class="portal-card-actions">
          ${canOpen
            ? '<button type="button" class="primary-inline" data-portal-tab="overview">概要を開く</button>'
            : '<span class="muted">サイドバーからプロジェクトを選ぶと詳細を確認できます。</span>'}
        </div>`;
    },
  });

  // 定常業務（cowork / kiro-loop / agent-loop）。
  registerPortalCard('routines', {
    order: 20,
    html: () => {
      const count = coworkItemCount();
      return `<div class="portal-card-heading">
          <span class="summary-kicker">繰り返す作業を任せる</span>
          <h3>定常業務</h3>
        </div>
        <p class="portal-card-count">${count
          ? `<strong>${count}</strong> 件の作業を登録済み`
          : '登録された作業はまだありません'}</p>
        <p class="muted">定期実行と手順つき作業を登録し、実行と履歴をここから確認します。</p>
        <div class="portal-card-actions">
          <button type="button" class="primary-inline" data-portal-tab="cowork">定常業務を開く</button>
        </div>`;
    },
  });

  // ミッション（agent-amigos）。ミッションも依頼先ホームも無い環境ではタブと同様に出さない。
  registerPortalCard('missions', {
    order: 30,
    html: () => {
      const missions = (state.amigos && state.amigos.missions) || [];
      const tabButton = $('tab-btn-amigos');
      const available = missions.length > 0 || (tabButton && !tabButton.hidden);
      if (!available) return '';
      return `<div class="portal-card-heading">
          <span class="summary-kicker">チームに依頼する</span>
          <h3>ミッション</h3>
        </div>
        <p class="portal-card-count">${missions.length
          ? `<strong>${missions.length}</strong> 件のミッション`
          : '進行中のミッションはありません'}</p>
        <p class="muted">依頼したミッションの進行・担当・納品をここから確認します。</p>
        <div class="portal-card-actions">
          <button type="button" class="primary-inline" data-portal-tab="amigos">ミッションを開く</button>
        </div>`;
    },
  });
}

// カードの定番遷移はコアが 1 か所で配線する（各カードは data 属性を書くだけ）。
//   data-portal-tab="<tab>"          … そのタブへ移動
//   data-portal-settings="<section>" … 全体設定の節へ移動
//   data-portal-needs="<dir>"        … プロジェクトを選んで要対応タブへ
function wirePortalHome(pane) {
  for (const btn of pane.querySelectorAll('[data-portal-tab]')) {
    btn.addEventListener('click', () => switchTab(btn.dataset.portalTab));
  }
  for (const btn of pane.querySelectorAll('[data-portal-settings]')) {
    btn.addEventListener('click', () => openGlobalSettings(btn.dataset.portalSettings));
  }
  for (const btn of pane.querySelectorAll('[data-portal-needs]')) {
    btn.addEventListener('click', async () => {
      await selectProject(btn.dataset.portalNeeds);
      switchTab('needs');
    });
  }
  for (const entry of portalCardEntries()) {
    if (typeof entry.wire !== 'function') continue;
    const container = pane.querySelector(`#portal-card-${CSS.escape(entry.id)}`);
    if (container) entry.wire(container);
  }
}

function renderHome() {
  const pane = $('tab-home');
  if (!pane) return;
  const model = portalHomeModel((state.discovery && state.discovery.projects) || []);
  const cards = portalCardEntries()
    .map((entry) => {
      let inner;
      try {
        inner = typeof entry.html === 'function' ? entry.html() : '';
      } catch (err) {
        uiLog('portal card failed', entry.id, String((err && err.message) || err));
        inner = '';
      }
      // '' は「この端末では使っていない面」——空のカードを並べない。
      if (!inner) return '';
      return `<article class="portal-card" id="portal-card-${esc(entry.id)}">${inner}</article>`;
    })
    .filter(Boolean)
    .join('');
  pane.innerHTML = `<section class="portal-home" aria-labelledby="portal-home-title">
    <header class="portal-home-header">
      <div>
        <span class="summary-kicker">ホーム</span>
        <h2 id="portal-home-title">いまの全体像</h2>
        <p>プロジェクト・定常業務・ミッション・参加できる仕事を横断して確認し、必要な画面へ移動します。</p>
      </div>
    </header>
    ${portalNeedsQueueHtml(model)}
    <div class="portal-cards">${cards}</div>
  </section>`;
  wirePortalHome(pane);
}
