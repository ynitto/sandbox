'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// ミッションタブ（agent-amigos ミッションの読み取りビュー）
// ---------------------------------------------------------------------------

async function refreshAmigos() {
  if (!api.amigosOverview) return;
  try {
    state.amigos = await api.amigosOverview();
  } catch (err) {
    state.amigos = { error: err.message, missions: [], budget: null, errors: [] };
  }
  updateAmigosTabVisibility();
}

// agent-amigos の設定ホームとミッションを、現在選択しているプロジェクトだけに限定する。
// home が無いミッションは全体バス由来で所属を確定できないため表示しない。
function amigosForProject(amigos, projectFolder) {
  const source = amigos || {};
  const key = coworkPathKey(projectFolder);
  if (!key) return { ...source, homes: [], missions: [], deliveries: [], errors: [] };
  const homes = (source.homes || []).filter(
    (home) => !!home.configFile && coworkPathKey(home.dir) === key
  );
  const allowed = new Set(homes.map((home) => coworkPathKey(home.dir)));
  const missions = (source.missions || []).filter((mission) => allowed.has(coworkPathKey(mission.home)));
  const deliveries = (source.deliveries || []).filter((d) => allowed.has(coworkPathKey(d.home)));
  // バスから消えた（gc 済み）ミッションの納品も、ミッションと同じ器で開けるようにする。
  const orphanMissions = (source.orphanDeliveries || [])
    .filter((d) => allowed.has(coworkPathKey(d.home)))
    .map((d) => ({
      id: d.mission,
      title: d.title || d.mission,
      goal: d.goal || '',
      phase: 'done',
      roles: [],
      messages: [],
      attentionCount: 0,
      home: d.home,
      delivery: d,
      archived: true,     // ミッションの経過は残っていない（成果物だけ）
    }));
  return { ...source, homes, missions, deliveries, orphanMissions, errors: [] };
}

// ミッションも依頼先ホームも無いときはタブを隠す（cowork と同じ流儀）。
function updateAmigosTabVisibility() {
  const btn = $('tab-btn-amigos');
  const pane = $('tab-amigos');
  if (!btn || !pane) return;
  const a = amigosForProject(state.amigos, selectedProjectFolder());
  const show = !!(
    a &&
    ((a.missions && a.missions.length) || (a.homes && a.homes.length))
  );
  btn.classList.toggle('hidden', !show);
  btn.hidden = !show;
  pane.classList.toggle('hidden', !show);
  pane.hidden = !show;
  if (!show && btn.classList.contains('active')) {
    const fallback = [...document.querySelectorAll('.tab')]
      .find((tab) => tab !== btn && !tab.hidden && !tab.classList.contains('hidden'));
    if (fallback) switchTab(fallback.dataset.tab);
  }
}

function amigosMin(sec) {
  return (Number(sec || 0) / 60).toFixed(1);
}

function amigosPhaseLabel(phase) {
  return (
    {
      open: '担当者を募集中',
      working: '作業中',
      integrating: '成果をまとめています',
      reviewing: '確認待ち',
      done: '完了',
      failed: '要確認',
      cancelled: '中止',
    }[phase] || phase
  );
}

function amigosWorkloadLabel(wl) {
  return (
    { routine: '定常業務', project: 'プロジェクト', flow: 'フロー', amigos: 'ミッション' }[wl] || wl
  );
}

function amigosNextStep(m) {
  return (
    {
      open: '担当者が揃うと作業を開始します。',
      working: '担当メンバーが作業を進めています。',
      integrating: '各担当の成果をひとつにまとめています。',
      reviewing: '成果の確認を待っています。',
      done: 'このミッションは完了しました。',
      failed: '作業を続けるために確認が必要です。',
      cancelled: 'このミッションは中止されました。',
    }[m.phase] || '状況を確認しています。'
  );
}

function amigosMemberStatus(role) {
  if (role.done) return '完了';
  if (!role.node) return '参加待ち';
  if (role.state === 'paused') return '一時停止';
  if (role.state === 'idle') return '待機中';
  return '作業中';
}

function amigosFriendlyNote(note) {
  return String(note || '')
    .replace(/\[node-budget\]\s*/gi, '利用時間の上限に達したため、')
    .replace(/\bamigo\b/gi, '担当エージェント')
    .trim();
}

function amigosMessageTypeLabel(type) {
  return (
    {
      question: '質問',
      answer: '回答',
      request: '依頼',
      review: '確認結果',
      'decision-request': '判断のお願い',
      status: '進捗共有',
      info: '共有',
      'wrap-up': 'まとめ',
      approve: '確認完了',
      feedback: '修正依頼',
    }[type] || '共有'
  );
}

function amigosMissionProgress(m) {
  const members = m.roles || [];
  const done = members.filter((r) => r.done).length;
  return { done, total: members.length };
}

function amigosMissionAttention(m) {
  const paused = (m.roles || []).filter((r) => r.state === 'paused').length;
  const count = Number(m.attentionCount || 0) + paused;
  if (!count) return '';
  return `<div class="amigos-attention" role="status">確認が必要な項目が ${count} 件あります</div>`;
}

function amigosMissionCardHtml(m) {
  const progress = amigosMissionProgress(m);
  const progressText = m.archived
    ? '経過は整理済み'
    : (progress.total ? `${progress.total}人中${progress.done}人が完了` : '担当者を確認中');
  const received = m.delivery
    ? `<span class="amigos-card-fact">成果物
        ${esc((m.delivery.files || []).filter((f) => f.exported).length)} 件
        ${m.delivery.partial ? '（一部）' : ''}</span>`
    : '';
  const goal = m.goal ? `<p class="amigos-card-goal">${esc(m.goal)}</p>` : '';
  return `<article class="amigos-mission-card">
    <div class="amigos-card-heading">
      <span class="amigos-phase amigos-phase-${esc(m.phase)}">${esc(amigosPhaseLabel(m.phase))}</span>
      <h3>${esc(m.title)}</h3>
    </div>
    <div class="amigos-card-summary">
      ${goal}
      <p class="amigos-next-step">${esc(amigosNextStep(m))}</p>
    </div>
    ${amigosMissionAttention(m)}
    <div class="amigos-card-footer">
      <div class="amigos-card-meta">
        <span class="amigos-card-fact">${esc(progressText)}</span>
      ${m.archived
        ? ''
        : ((m.messages || []).length
          ? `<span class="amigos-card-fact">やりとり ${(m.messages || []).length} 件</span>`
          : '<span class="amigos-card-fact">やりとり 0 件</span>')}
        ${received}
      </div>
      <div class="amigos-card-actions">
        <button type="button" class="primary-inline" data-amigos-detail="${esc(m.id)}">
          ${m.delivery ? '成果物を見る' : '詳しく見る'}</button>
      </div>
    </div>
  </article>`;
}

function amigosMemberHtml(role, mission) {
  const active = !['done', 'failed', 'cancelled'].includes(mission.phase);
  const claim = !role.node && mission.home && active
    ? `<button type="button" class="amigos-claim-btn" data-home="${esc(mission.home)}"
        data-mission="${esc(mission.id)}" data-role="${esc(role.id)}">この担当を引き受ける</button>`
    : '';
  return `<article class="amigos-member${role.state === 'paused' ? ' is-paused' : ''}">
    <div class="amigos-member-heading">
      <h4>${esc(role.displayName || role.title || '担当メンバー')}</h4>
      <span class="amigos-member-status">${esc(amigosMemberStatus(role))}</span>
    </div>
    ${role.responsibility ? `<p>${esc(role.responsibility)}</p>` : '<p class="muted">担当内容を確認中です。</p>'}
    ${role.note ? `<p class="amigos-member-note">${esc(amigosFriendlyNote(role.note))}</p>` : ''}
    ${claim}
  </article>`;
}

function amigosMessageHtml(message) {
  const route = message.toLabel === '全員'
    ? `${message.fromLabel}から全員へ`
    : `${message.fromLabel}から${message.toLabel}へ`;
  const answered = message.type === 'question' && message.answered
    ? '<span class="amigos-message-answer">回答済み</span>'
    : '';
  const attention = message.requiresAttention ? ' is-attention' : '';
  return `<details class="amigos-message${attention}">
    <summary>
      <span class="amigos-message-kind">${esc(amigosMessageTypeLabel(message.type))}</span>
      <span class="amigos-message-summary"><strong>${esc(message.summary || '内容を確認')}</strong><small>${esc(route)}</small></span>
      ${answered}
    </summary>
    <div class="amigos-message-body">
      ${message.body ? `<p>${esc(message.body)}</p>` : '<p class="muted">本文はありません。</p>'}
      ${message.createdAt ? `<time datetime="${esc(message.createdAt)}">${esc(fmtTime(message.createdAt))}</time>` : ''}
    </div>
  </details>`;
}

// 成果物はプログラムに限らないので、文書は本文、画像は縮小表示、
// それ以外はメタ情報だけを見せる。受入待ちと受け取り済みで同じプレビューを使う。
function amigosDeliverableFileHtml(file) {
  const meta = `<small>${esc(file.role || '')}${file.role ? ' ・ ' : ''}${esc(fmtLogSize(file.bytes))}</small>`;
  let body;
  if (file.kind === 'image') {
    body = `<img class="amigos-preview-image" src="${esc(file.dataUri)}" alt="${esc(file.path)}">`;
  } else if (file.kind === 'markdown') {
    body = `<div class="amigos-preview-doc">${mdToHtml(file.text || '')}</div>`;
  } else if (file.kind === 'text') {
    body = `<pre class="amigos-preview-text">${esc(file.text || '')}</pre>`;
  } else {
    body = '<p class="muted">この形式は画面で表示できません。受け入れるとファイルとして受け取れます。</p>';
  }
  const cut = file.truncated ? '<p class="muted">長いため一部だけ表示しています。</p>' : '';
  return `<div class="amigos-preview-heading">
      <strong class="amigos-preview-path">${esc(file.path)}</strong>${meta}
    </div>
    <div class="amigos-preview-body">${body}${cut}</div>`;
}

function amigosArtifactWorkspaceHtml(files, label) {
  const entries = files || [];
  if (!entries.length) return '<div class="empty compact">成果物のファイルがありません。</div>';
  const buttons = entries.map((file, index) => `<button type="button" class="amigos-artifact-file${index === 0 ? ' active' : ''}"
      data-amigos-file="${index}" aria-current="${index === 0 ? 'true' : 'false'}"
      title="${esc(file.path)} の内容を表示">
      <span>${esc(file.path)}</span>
      <small>${esc(file.role || '')}${file.role ? ' ・ ' : ''}${esc(fmtLogSize(file.bytes))}</small>
    </button>`).join('');
  return `<div class="amigos-artifact-workspace">
    <aside class="amigos-artifact-files" aria-label="${esc(label || '成果物')}のファイル一覧">
      <div class="amigos-artifact-files-title"><strong>ファイル</strong><span>${entries.length} 件</span></div>
      <div class="amigos-artifact-file-list">${buttons}</div>
    </aside>
    <section class="amigos-artifact-preview" aria-label="選択したファイルの内容" aria-live="polite">
      ${amigosDeliverableFileHtml(entries[0])}
    </section>
  </div>`;
}

function setupAmigosArtifactWorkspace(root, files) {
  if (!root) return;
  const entries = files || [];
  const previewPane = root.querySelector('.amigos-artifact-preview');
  for (const btn of root.querySelectorAll('[data-amigos-file]')) {
    btn.addEventListener('click', () => {
      const file = entries[Number(btn.dataset.amigosFile)];
      if (!file || !previewPane) return;
      for (const item of root.querySelectorAll('[data-amigos-file]')) {
        const selected = item === btn;
        item.classList.toggle('active', selected);
        item.setAttribute('aria-current', selected ? 'true' : 'false');
      }
      previewPane.innerHTML = amigosDeliverableFileHtml(file);
    });
  }
}

// 受け取り済みの成果物（納品棚）。中身は開いたときに読むので、ここは器だけ描く。
function amigosReceivedSectionHtml(m) {
  const d = m.delivery;
  if (!d) return '';
  const partial = d.partial
    ? '<p class="muted">予算や作業の打ち切りにより、一部だけの成果物です。</p>'
    : '';
  const refs = (d.files || []).filter((f) => !f.exported);
  const refNote = refs.length
    ? `<p class="muted">${refs.length} 件は大きいため保存せず、参照だけ記録しています:
        ${esc(refs.map((f) => f.path).join(', '))}</p>`
    : '';
  const code = d.code
    ? `<p class="muted">コードは ${esc(d.code.repo || '')} の
        ${esc(d.code.branch || '')} にあります。</p>`
    : '';
  const exported = (d.files || []).filter((file) => file.exported).length;
  return `<details class="amigos-received-section amigos-detail-section">
    <summary>
      <span class="amigos-received-title">受け取った成果物</span>
      <span class="amigos-received-summary">${esc(fmtTime(d.acceptedAt))} ・ ${exported} ファイル${d.partial ? ' ・ 一部のみ' : ''}</span>
    </summary>
    <div class="amigos-received-content">
      <p class="muted">実行時間 ${esc(amigosMin(d.executionSeconds))} 分</p>
      ${partial}
      <div class="amigos-preview-list" id="amigos-received-body">
        <div class="empty compact">開くと成果物を読み込みます。</div>
      </div>
      ${refNote}
      ${code}
      <div class="amigos-accept-actions">
        <button type="button" data-amigos-open="${esc(d.dir)}">納品棚を開く</button>
        <button type="button" class="primary-inline" data-amigos-export="${esc(m.id)}"
          data-home="${esc(d.home || m.home || '')}">別フォルダへ保存</button>
      </div>
      <p class="amigos-export-status muted" data-amigos-export-status aria-live="polite"></p>
    </div>
  </details>`;
}

function amigosIntegrationHtml(m) {
  const integration = m && m.integration;
  if (!integration) return '';
  return `<div class="amigos-integration amigos-integration-${esc(integration.status)}" role="status">
    <span>自動処理</span><strong>${esc(integration.label)}</strong>
  </div>`;
}

function amigosDeliverableSectionHtml(m) {
  if (m.phase !== 'reviewing' || !m.deliverable) return '';
  const files = m.deliverable.files || [];
  const partial = m.manifest && m.manifest.partial
    ? '<p class="amigos-attention" role="status">予算や作業の打ち切りにより、一部だけの成果物です。</p>'
    : '';
  const more = m.deliverable.truncated
    ? `<p class="muted">全 ${Number(m.deliverable.total || 0)} 件のうち先頭だけ表示しています。</p>`
    : '';
  const list = files.length
    ? amigosArtifactWorkspaceHtml(files, '受入待ち成果物')
    : '<div class="empty compact">成果物のファイルがありません。</div>';
  const actions = m.home
    ? `<div class="amigos-accept-actions">
        <button type="button" class="primary-inline" data-amigos-accept="${esc(m.id)}"
          data-home="${esc(m.home)}">この成果を受け取る</button>
        <button type="button" data-amigos-reject="${esc(m.id)}"
          data-home="${esc(m.home)}">修正を依頼する</button>
      </div>
      <p class="muted">受け取ると成果物が納品棚（deliveries フォルダ）へ保存されます。</p>`
    : '<p class="muted">このミッションの受け取り操作はオーナーの端末から行います。</p>';
  return `<section class="amigos-detail-section">
    <h3>成果物の確認</h3>
    ${partial}
    <div class="amigos-preview-list">${list}</div>
    ${more}
    ${actions}
  </section>`;
}

function amigosMissionDetailHtml(m) {
  const progress = amigosMissionProgress(m);
  const progressText = m.archived
    ? '経過は整理済み'
    : (progress.total ? `${progress.total}人中${progress.done}人が完了` : '担当者を確認中');
  const members = (m.roles || []).map((role) => amigosMemberHtml(role, m)).join('');
  const conversation = (m.messages || []).length
    ? (m.messages || []).map(amigosMessageHtml).join('')
    : '<div class="empty compact">まだやりとりはありません。</div>';
  // 経過が整理済みのものは、担当・やりとりの節を出さない（空欄を並べない）
  const progressSections = m.archived ? '' : `
    <section class="amigos-detail-section">
      <h3>メンバーの作業状況</h3>
      <div class="amigos-member-grid">${members || '<div class="empty compact">担当者を確認中です。</div>'}</div>
    </section>
    <section class="amigos-detail-section">
      <h3>やりとり</h3>
      <p class="muted">要点を時系列で表示しています。発言を選ぶと全文を確認できます。</p>
      <div class="amigos-conversation">${conversation}</div>
    </section>`;
  return `<div class="amigos-detail-content">
    <section class="amigos-detail-overview">
      <div class="amigos-detail-overview-head">
        <h3>現在の状況</h3>
        ${amigosMissionAttention(m)}
      </div>
      <div class="amigos-detail-status">
        <span class="amigos-phase amigos-phase-${esc(m.phase)}">${esc(amigosPhaseLabel(m.phase))}</span>
        <strong>${esc(progressText)}</strong>
      </div>
      ${amigosIntegrationHtml(m)}
      ${m.goal ? `<p class="amigos-detail-goal">${esc(m.goal)}</p>` : ''}
      <p>${esc(m.archived
        ? 'このミッションの経過は整理済みで、受け取った成果物だけが残っています。'
        : amigosNextStep(m))}</p>
    </section>
    ${amigosReceivedSectionHtml(m)}
    ${amigosDeliverableSectionHtml(m)}
    ${progressSections}
  </div>`;
}

function setupAmigosOpenButtons(root) {
  for (const btn of root.querySelectorAll('[data-amigos-open]')) {
    btn.addEventListener('click', () =>
      guard('保存先を開く', () => api.openPath(btn.dataset.amigosOpen))
    );
  }
}

function setupAmigosExportButtons(root) {
  for (const btn of root.querySelectorAll('[data-amigos-export]')) {
    btn.addEventListener('click', () =>
      guard('成果物の保存', async () => {
        const status = btn.closest('.amigos-detail-section').querySelector('[data-amigos-export-status]');
        btn.disabled = true;
        if (status) status.textContent = '保存先を選択してください。';
        try {
          const result = await api.amigosDeliveryExport(btn.dataset.home, btn.dataset.amigosExport);
          if (result.canceled) {
            if (status) status.textContent = '保存をキャンセルしました。';
            return;
          }
          const notes = [];
          if (result.skipped) notes.push(`参照のみ ${result.skipped} 件は対象外`);
          if (result.missing) notes.push(`見つからないファイル ${result.missing} 件`);
          const message = `${result.copied} 件を ${result.target} へ保存しました${notes.length ? `（${notes.join('、')}）` : ''}。`;
          if (status) status.textContent = message;
          toast('成果物を別フォルダへ保存しました', true);
        } finally {
          btn.disabled = false;
        }
      })
    );
  }
}

function setupAmigosClaimButtons(root) {
  for (const btn of root.querySelectorAll('.amigos-claim-btn')) {
    btn.addEventListener('click', () =>
      guard('担当の引き受け', async () => {
        btn.disabled = true;
        await api.amigosClaim(btn.dataset.home, btn.dataset.mission, btn.dataset.role);
        toast('担当の引き受けを依頼しました', true);
      })
    );
  }
}

// 受入判定は commands 投函（accept / reject）で owner デーモンへ委ねる。
// dashboard がバスへ直接書くことはない。
function setupAmigosAcceptButtons(root) {
  for (const btn of root.querySelectorAll('[data-amigos-accept]')) {
    btn.addEventListener('click', () =>
      guard('成果の受け取り', async () => {
        btn.disabled = true;
        await api.amigosAccept(btn.dataset.home, btn.dataset.amigosAccept);
        $('dlg-amigos-detail').close();
        toast('受け取りを依頼しました（納品棚へ保存されます）', true);
        await refreshAmigos();
        renderAmigos();
      })
    );
  }
  for (const btn of root.querySelectorAll('[data-amigos-reject]')) {
    btn.addEventListener('click', () =>
      openAmigosRejectDialog(btn.dataset.home, btn.dataset.amigosReject)
    );
  }
}

// 修正依頼の入力。Electron の renderer には window.prompt が無いので、
// 他のダイアログと同じ <dialog> + form で受ける。
function openAmigosRejectDialog(home, missionId) {
  const dlg = $('dlg-amigos-reject');
  if (!dlg) return;
  state.amigosReject = { home, missionId };
  $('amigos-reject-feedback').value = '';
  dlg.showModal();
  $('amigos-reject-feedback').focus();
}

// 受け取り済み成果物の中身は詳細を開いたときに読む（一覧のポーリングで
// 全ミッションの全文・画像を運ばない）。
async function loadAmigosReceived(mission) {
  const box = $('amigos-received-body');
  if (!box || !mission.delivery || !api.amigosDeliveryContents) return false;
  box.innerHTML = '<div class="empty compact">読み込んでいます…</div>';
  try {
    const got = await api.amigosDeliveryContents(mission.delivery.home, mission.id);
    const files = got.files || [];
    box.innerHTML = files.length
      ? amigosArtifactWorkspaceHtml(files, '受け取った成果物')
      : '<div class="empty compact">保存されたファイルがありません（参照のみの納品です）。</div>';
    setupAmigosArtifactWorkspace(box, files);
    if (got.truncated) {
      box.insertAdjacentHTML('beforeend',
        `<p class="muted">全 ${Number(got.total || 0)} 件のうち先頭だけ表示しています。</p>`);
    }
    return true;
  } catch (err) {
    box.innerHTML = `<div class="empty compact">成果物を読み込めませんでした: ${esc(err.message)}</div>`;
    return false;
  }
}

function setupAmigosReceived(root, mission) {
  const details = root && root.querySelector('.amigos-received-section');
  if (!details || !mission.delivery) return;
  details.addEventListener('toggle', async () => {
    if (!details.open || details.dataset.loadState) return;
    details.dataset.loadState = 'loading';
    const loaded = await loadAmigosReceived(mission);
    details.dataset.loadState = loaded ? 'done' : '';
  });
}

function openAmigosDetail(missionId) {
  const scoped = amigosForProject(state.amigos, selectedProjectFolder());
  const mission = (scoped.missions || []).find((m) => m.id === missionId)
    || (scoped.orphanMissions || []).find((m) => m.id === missionId);
  if (!mission) return;
  $('amigos-detail-title').textContent = mission.title || 'ミッション詳細';
  $('amigos-detail-body').innerHTML = amigosMissionDetailHtml(mission);
  setupAmigosClaimButtons($('amigos-detail-body'));
  setupAmigosAcceptButtons($('amigos-detail-body'));
  setupAmigosOpenButtons($('amigos-detail-body'));
  setupAmigosExportButtons($('amigos-detail-body'));
  setupAmigosArtifactWorkspace($('amigos-detail-body'), (mission.deliverable || {}).files || []);
  setupAmigosReceived($('amigos-detail-body'), mission);
  $('dlg-amigos-detail').showModal();
}

const AMIGOS_ROLES_SAMPLE = JSON.stringify(
  [
    { id: 'architect', mission: '設計を確定し質問に回答する', deliverables: ['architecture.md'] },
    { id: 'impl', mission: '実装する', deliverables: ['src/'], collaborates_with: ['architect'] },
    { id: 'reviewer', mission: '成果物をレビューする', approver: true },
  ],
  null,
  2
);

function openAmigosRequestDialog() {
  const dlg = $('dlg-amigos-post');
  if (!dlg) return;
  const homes = amigosForProject(state.amigos, selectedProjectFolder()).homes || [];
  const sel = $('amigos-post-home');
  sel.innerHTML = homes
    .map((h, index) => `<option value="${esc(h.dir)}">${esc(coworkRepoLabel(h.dir) || `実行先 ${index + 1}`)}</option>`)
    .join('');
  if (!$('amigos-post-roles').value.trim()) $('amigos-post-roles').value = AMIGOS_ROLES_SAMPLE;
  dlg.showModal();
}

function setupAmigosRejectDialog() {
  const dlg = $('dlg-amigos-reject');
  if (!dlg) return;
  $('btn-amigos-reject-cancel').addEventListener('click', () => dlg.close());
  dlg.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const target = state.amigosReject || {};
    const feedback = $('amigos-reject-feedback').value.trim();
    if (!feedback) {
      toast('修正してほしい内容を入力してください');
      return;
    }
    guard('修正の依頼', async () => {
      await api.amigosReject(target.home, target.missionId, feedback);
      dlg.close();
      $('dlg-amigos-detail').close();
      toast('修正依頼を送りました（担当が作業をやり直します）', true);
      await refreshAmigos();
      renderAmigos();
    });
  });
}

function setupAmigosDialogs() {
  setupAmigosRejectDialog();
  const dlg = $('dlg-amigos-post');
  if (!dlg) return;
  $('btn-amigos-post-cancel').addEventListener('click', () => dlg.close());
  $('btn-amigos-detail-close').addEventListener('click', () => $('dlg-amigos-detail').close());
  dlg.addEventListener('submit', (ev) => {
    ev.preventDefault();
    guard('タスクの依頼', async () => {
      let roles;
      try {
        roles = JSON.parse($('amigos-post-roles').value);
      } catch (e) {
        toast(`担当チームの設定を読み取れません: ${e.message}`);
        return;
      }
      await api.amigosRequest({
        home: $('amigos-post-home').value,
        title: $('amigos-post-title').value.trim(),
        goal: $('amigos-post-goal').value.trim(),
        design: $('amigos-post-design').value,
        roles,
      });
      dlg.close();
      toast('依頼を投函しました（常駐デーモンが取り込み、公示します）', true);
      await refreshAmigos();
      renderAmigos();
    });
  });
}

function renderAmigos() {
  const el = $('tab-amigos');
  if (!el) return;
  updateAmigosTabVisibility();
  const a = amigosForProject(state.amigos, selectedProjectFolder());
  if (!a) return;
  if (a.error) {
    el.innerHTML = `<div class="empty"><strong>ミッションを読み込めませんでした</strong><span>${esc(a.error)}</span></div>`;
    return;
  }
  const missions = a.missions || [];
  const missionsHtml = missions.length
    ? `<div class="amigos-mission-grid">${missions.map(amigosMissionCardHtml).join('')}</div>`
    : `<div class="empty"><strong>ミッションがありません</strong>
        <span>新しい作業を始めるには「ミッションを依頼」を選んでください。</span></div>`;
  const errorsHtml = (a.errors || []).length
    ? '<p class="muted">一部のミッションを読み込めませんでした。更新しても直らない場合は詳細情報を確認してください。</p>'
    : '';
  // 成果物はミッションの中で見せる（利用者が考える単位はミッション）。ここに残すのは
  // ミッションの経過が整理済みで、成果物だけが残っているものだけ。
  const archived = a.orphanMissions || [];
  const archivedHtml = archived.length
    ? `<section>
        <h3>過去の成果物（${archived.length} 件）</h3>
        <p class="muted">ミッションの経過は整理済みで、受け取った成果物だけが残っています。</p>
        <div class="amigos-mission-grid">${archived.map(amigosMissionCardHtml).join('')}</div>
      </section>`
    : '';
  const shelfPaths = (a.homes || []).map((h) => `${h.dir}/deliveries`);
  const shelfHint = (a.homes || []).length
    ? `<p class="muted">受け取った成果物はミッションを開くと中身を確認できます。
        保存先: ${esc(shelfPaths.join(' / '))}</p>`
    : '';

  // 投函した指示は常駐デーモンが取り込んで初めて効く。溜まったままなら黙って
  // 失敗しているのと同じなので、画面で知らせる。
  const pending = (a.homes || []).reduce((n, h) => n + (Number(h.pendingCommands) || 0), 0);
  const pendingHtml = pending
    ? `<div class="amigos-attention" role="status">
        依頼した操作が ${pending} 件、まだ実行されていません。
        担当エージェントの常駐（agent-amigos）が停止している可能性があります。
      </div>`
    : '';
  el.innerHTML = `
    <div class="amigos-shell">
      <header class="cowork-header">
        <div>
          <span class="summary-kicker">協働</span>
          <h2>ミッション</h2>
          <p class="muted">複数の担当メンバーで進める作業の状況を確認できます。</p>
        </div>
        <div class="row">
          ${(a.homes || []).length ? '<button id="btn-amigos-request">ミッションを依頼</button>' : ''}
          <button id="btn-amigos-refresh">更新</button>
        </div>
      </header>
      ${pendingHtml}
      <section>
        <h3>ミッション（${missions.length} 件）</h3>
        ${missionsHtml}
        ${shelfHint}
        ${errorsHtml}
      </section>
      ${archivedHtml}
    </div>`;

  const refreshBtn = $('btn-amigos-refresh');
  if (refreshBtn)
    refreshBtn.addEventListener('click', () =>
      guard('ミッション更新', async () => {
        await refreshAmigos();
        renderAmigos();
      })
    );
  const requestBtn = $('btn-amigos-request');
  if (requestBtn) requestBtn.addEventListener('click', () => openAmigosRequestDialog());
  for (const btn of el.querySelectorAll('[data-amigos-detail]')) {
    btn.addEventListener('click', () => openAmigosDetail(btn.dataset.amigosDetail));
  }
  setupAmigosOpenButtons(el);
}

function workTypeLabel(type) {
  return type === 'state-machine' ? '定型業務' : '定期実行';
}
