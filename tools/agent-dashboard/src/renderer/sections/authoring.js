'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// オーサリング: 新規プロジェクト作成・プロジェクトファイル編集
// ---------------------------------------------------------------------------

// 既知プロジェクトの親フォルダ ＋ 設定 roots の親（新規作成先の候補）
function knownRoots() {
  const roots = new Set();
  for (const p of state.discovery.projects || []) {
    if (p.dir) roots.add(p.dir.replace(/[\\/][^\\/]+$/, ''));
  }
  for (const r of (state.config && state.config.projects && state.config.projects.roots) || []) {
    if (r) roots.add(String(r).replace(/[\\/][^\\/]+$/, ''));
  }
  return [...roots].filter(Boolean);
}

// 新規プロジェクトの repos 行を 1 つ追加する（任意・複数可）。
// path はモノレポ内の担当フォルダ＝同じ URL を役割別に複数エントリへ分ける識別子
// （schemas/repos.schema.json の (url, path, base) identity）。target は MR/PR 先（省略=base）。
function addRepoRow(prefill = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'np-repo-row';
  wrap.innerHTML = `
    <input class="np-r-name mono" placeholder="名前" value="${esc(prefill.name || '')}" />
    <input class="np-r-url mono" placeholder="git URL（必須）" value="${esc(prefill.url || '')}" />
    <input class="np-r-base mono" placeholder="ベース 例 main" value="${esc(prefill.base || '')}" />
    <input class="np-r-target mono" placeholder="MR先（省略=ベース）" value="${esc(prefill.target || '')}" />
    <input class="np-r-path mono" placeholder="モノレポ内フォルダ 例 apps/api" value="${esc(prefill.path || '')}" />
    <input class="np-r-owns mono" placeholder="担当範囲（省略=参照のみ）" value="${esc(prefill.owns || '')}" />
    <input class="np-r-desc" placeholder="説明" value="${esc(prefill.desc || '')}" />
    <button type="button" class="np-r-del" title="この行を削除">✕</button>`;
  wrap.querySelector('.np-r-del').addEventListener('click', () => wrap.remove());
  $('np-repos').appendChild(wrap);
}

function openNewProject() {
  const roots = knownRoots();
  $('np-root-list').innerHTML = roots.map((r) => `<option value="${esc(r)}"></option>`).join('');
  $('np-root').value = state.selectedDir
    ? state.selectedDir.replace(/[\\/][^\\/]+$/, '') || roots[0] || ''
    : roots[0] || '';
  $('np-name').value = '';
  if ($('np-charter')) $('np-charter').value = '';
  $('np-goal').value = '';
  $('np-memo').value = '';
  $('np-deliverables').value = '';
  $('np-constraints').value = '';
  $('np-assumptions').value = '';
  $('np-acceptance').value = '';
  $('np-repos').innerHTML = '';
  $('np-ai-status').textContent = '';
  $('btn-np-ai').disabled = false;
  $('dlg-new-project').showModal();
}

// フォームの書きかけ（goal・自由メモ・各欄）からエージェントに各セクションを
// 下書きさせ、返ってきたフィールドだけを流し込む（応答はテキストのみ・保存はしない）。
// 新規作成時はまだプロジェクトが無いので、CLI の解決は ⚙ 設定 → 既定 kiro。
async function aiDraftCharter() {
  const btn = $('btn-np-ai');
  const status = $('np-ai-status');
  const spec = {
    name: $('np-name').value.trim() || ($('np-charter') ? $('np-charter').value.trim() : ''),
    goal: $('np-goal').value,
    memo: $('np-memo').value,
    deliverables: $('np-deliverables').value,
    constraints: $('np-constraints').value,
    assumptions: $('np-assumptions').value,
    acceptance: $('np-acceptance').value,
  };
  if (!spec.goal.trim() && !spec.memo.trim()) {
    return toast('目標か自由メモに、やりたいことを一言書いてから実行してください');
  }
  btn.disabled = true;
  status.textContent = 'エージェントに問い合わせ中…（モデル応答まで数十秒かかることがあります）';
  try {
    const res = await api.agentCharter({ mode: 'draft', spec });
    const f = res.fields || {};
    if (f.goal) $('np-goal').value = f.goal;
    if (f.deliverables) $('np-deliverables').value = f.deliverables;
    if (f.constraints) $('np-constraints').value = f.constraints;
    if (f.assumptions) $('np-assumptions').value = f.assumptions;
    if (f.acceptance) $('np-acceptance').value = f.acceptance;
    status.textContent = `下書きしました（${res.cli}${res.model ? ` / ${res.model}` : ''}）— 内容を確認・修正してから作成してください`;
  } catch (err) {
    status.textContent = '';
    toast(`AI 下書きに失敗しました: ${err.message || err}`);
  } finally {
    btn.disabled = false;
  }
}

async function submitNewProject() {
  const repos = [...document.querySelectorAll('#np-repos .np-repo-row')]
    .map((row) => ({
      name: row.querySelector('.np-r-name').value.trim(),
      url: row.querySelector('.np-r-url').value.trim(),
      base: row.querySelector('.np-r-base').value.trim(),
      target: row.querySelector('.np-r-target').value.trim(),
      path: row.querySelector('.np-r-path').value.trim(),
      owns: row.querySelector('.np-r-owns').value.trim(),
      desc: row.querySelector('.np-r-desc').value.trim(),
    }))
    .filter((r) => r.url);
  const spec = {
    root: $('np-root').value.trim(),
    name: $('np-name').value.trim(),
    charterName: $('np-charter') ? $('np-charter').value.trim() : '',
    goal: $('np-goal').value,
    deliverables: $('np-deliverables').value,
    constraints: $('np-constraints').value,
    assumptions: $('np-assumptions').value,
    acceptance: $('np-acceptance').value,
    repos,
    // 新規プロジェクトはマスター運用で作る: charter.md は全バージョン共通の憲章（分解されない）、
    // やるべきことは計画バージョン（charters/<名前>.md）に書く。
    master: true,
  };
  const res = await guard('プロジェクト作成', async () => {
    const r = await api.createProject(spec);
    toast(`作成しました: ${r.dir}`, true);
    return r;
  });
  if (!res) return;
  // 発見対象に入るよう、作成したプロジェクトルートを設定 roots に追加する
  // （discovery は config roots を resolve して並べるため、生パスの追加で表示される）
  const known = (state.discovery.projects || []).some((p) => p.dir === res.dir);
  if (!known) {
    const cfg = state.config;
    cfg.projects = cfg.projects || {};
    cfg.projects.roots = cfg.projects.roots || [];
    if (!cfg.projects.roots.includes(res.dir)) {
      cfg.projects.roots.push(res.dir);
      state.config = await api.saveConfig(cfg);
    }
  }
  gitPushAfterWrite(`agent-dashboard: create project ${spec.name}`, res.dir);
  $('dlg-new-project').close();
  await refreshDiscovery();
  await selectProject(res.dir);
}

// charter.md / policy.md / repos.json の直接編集ダイアログを開く。
// これらは agent-project の「人が書く入力」— 編集して保存すると次の run で後段
// （backlog 生成・ルーティング）に反映される。タスク状態は編集対象にしない。
// charter ファイル（charter.md / charters/<name>.md）か。編集ダイアログの
// 入力補助（雛形挿入・AI 補完・セクションガイド）を出すかどうかの判定に使う
function isCharterFile(name) {
  return name === 'charter.md' || /^charters\/[^/\\]+\.md$/.test(name);
}
