'use strict';

// 過去セッション・run の流用実行（docs/plans/2026-08-31-agent-session-reuse-rerun-design.md）。
//
// 担当は 3 つで、どれも**新しい実行系・状態ストアを作らない**:
//   §2 蒸留   … 素の CLI セッション／過去 run を種に、AI が要求文またはワークフロー下書きを
//               作る（確定は人）。transcript 本文は下書きの材料にするだけで、inbox・
//               保存形・state repo のどこにも書かない（agent-audit の不変条件）。
//   §3 系譜   … 蒸留物には複製元 `source`（session/<cli>/<id> ／ run/<run-id>）を付ける。
//   §5 バッチ … パラメータ行 × テンプレート → n 本の adhoc run。行ごとに workspace を
//               持つので「1 run = 1 workspace」は崩さず、リポジトリまたぎを満たす。
//               件数上限と概算予算の確認を投函前に必ず通す（C1・C7）。
//
// 実行はどれも既存の adhoc.submit（inbox 投函 → agent-flow 起動）に載せる。

const path = require('path');

const adhoc = require('./adhoc');
const audit = require('../../agent-audit/main/audit');
const agent = require('../../agent-project/main/agent');
const budget = require('../../orchestration/main/budget');
const templateParameters = require('../../../base/main/template-parameters');

// バッチの件数上限。「すべての反復は有界」を投函口でも保つための固い上限で、
// 1 回の確認で人が読み切れる量でもある（C1）。
const BATCH_MAX_ROWS = 20;
// 実測が無いときの 1 run あたりのトークン概算。台帳（agent-budget）に flow の実績が
// あればそちらを使い、無いときだけこの数字で「桁」を示す（0 件と言い切らない）。
const FALLBACK_TOKENS_PER_RUN = 120000;

// 蒸留物の 3 形。どれも「もう一度実行できる形」だが、載る機構が違う。
//   request      … 1 回だけの adhoc 投函
//   workflow     … 保存形ワークフロー（agent-flow の工程グラフ）
//   statemachine … 定常業務（statemachine-use のステートマシン）。繰り返し・定期実行が要る
//                  手順向けで、**YAML を書くのは statemachine-use スキル**（下記）
const DRAFT_KINDS = ['request', 'workflow', 'statemachine'];

function draftKind(raw) {
  const kind = String(raw || '').trim();
  return DRAFT_KINDS.includes(kind) ? kind : 'request';
}

// `.statemachine/<machine>/` のフォルダ名。cowork の generateStateMachine と同じ規則で、
// 受け側でも同じ検査が走る（ここは投入前に人へ直させるための整形）。
function sanitizeMachineId(raw, fallback = 'routine') {
  const clean = String(raw || '').trim().toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, '-').replace(/^[-.]+|[-.]+$/g, '');
  return clean && clean !== '.' && clean !== '..' ? clean.slice(0, 60) : fallback;
}

// --- 蒸留（セッション → 再利用できる要求文 / ワークフロー） -------------------

function transcriptDigest(session) {
  const messages = Array.isArray(session && session.messages) ? session.messages : [];
  if (!messages.length) return '';
  // プロンプトへ載せる本文の総量を抑える。往復が多い会話は「最初の指示」と「最後の
  // やり取り」に意図が出るので、前後から詰める（中間は落とす）。
  const head = messages.slice(0, 6);
  const tail = messages.length > 12 ? messages.slice(-6) : [];
  const picked = tail.length ? [...head, { role: '', text: '…（中略）…' }, ...tail] : messages.slice(0, 12);
  const lines = picked.map((item) => {
    const role = String((item && item.role) || '').trim();
    const text = String((item && item.text) || '').trim().slice(0, 2000);
    if (!text) return '';
    return role ? `[${role}] ${text}` : text;
  }).filter(Boolean);
  return lines.join('\n\n').slice(0, 24000);
}

const DISTILL_RULES = [
  '- 出力は JSON オブジェクト1個だけ（前置き・説明・コードフェンスなし）。',
  '- 会話の再生ではなく、**もう一度別の対象へ実行できる依頼**へ一般化する。',
  '- 会話に出た秘密情報・アクセストークン・個人名・ローカル絶対パスは書き写さない。',
  '- 対象を差し替えたい箇所は `{{key}}` のプレースホルダーにする（key は英小文字とアンダースコア）。',
  '- 実在が確認できないリポジトリ名・コマンド・ファイルパスを発明しない。',
].join('\n');

function requestDraftPrompt(body, hint) {
  return 'あなたは過去の作業ログから、再利用できる作業依頼文を起こす編集者です。\n'
    + '次の記録を読み、同じ種類の作業をもう一度依頼するための依頼文を1件作ってください。\n\n'
    + `規則:\n${DISTILL_RULES}\n`
    + '- 形式: {"title":"20字程度の作業名","request":"依頼文（Markdown可）",'
    + '"parameters":["{{key}} で使ったキー名"]}\n'
    + '- request には目的・変更対象・受入基準・検証方法を、分かる範囲で節に分けて書く。\n\n'
    + (String(hint || '').trim() ? `利用者の補足:\n${String(hint).trim()}\n\n` : '')
    + `記録:\n${body}`;
}

function workflowDraftPrompt(body, hint, kinds) {
  return 'あなたは過去の作業ログから、再利用できる作業フロー（工程グラフ）を起こす設計者です。\n'
    + '次の記録を読み、同じ種類の作業を再現できる工程を組み立ててください。\n\n'
    + `規則:\n${DISTILL_RULES}\n`
    + '- 形式: {"name":"フロー名","description":"一文の説明","request":"このフローへ渡す既定の依頼文",'
    + '"nodes":[{"id":"英数字とハイフン","label":"表示名","goal":"その工程で行うこと",'
    + '"kind":"工程種別","deps":["先行工程のid"]}]}\n'
    + `- kind は次のいずれか: ${kinds.join(' / ')}。人の確認を挟む human は使わない。\n`
    + '- 工程は 2〜6 件。依存は循環させず、最初の工程の deps は空配列にする。\n'
    + '- goal は単独で読んで実行できる命令文にする。会話の経緯は書かない。\n\n'
    + (String(hint || '').trim() ? `利用者の補足:\n${String(hint).trim()}\n\n` : '')
    + `記録:\n${body}`;
}

// 定常業務（statemachine-use）の下書き。**YAML は書かせない。**
// `.statemachine/<machine>/workflow.yaml` の書式の正典は statemachine-use スキルで、
// 生成もそのスキルの作成モードが行う（cowork.generateStateMachine が対話 CLI を起こす）。
// ここで作るのは作成モードへ渡す**手順の指示文**だけ——dashboard が YAML を組み立てると、
// スキルとダッシュボードでステートマシンの書式が 2 実装になる（C7）。
//
// 指示文の形はスキルの分解原則に合わせる（SKILL.md「手順を状態遷移として分解する」）:
// 1 ステート 1 成果物・成功は機械が測れる形・分岐条件はステートではなく遷移に書く。
function statemachineDraftPrompt(body, hint) {
  return 'あなたは過去の作業ログから、繰り返し実行できる定型業務の手順書を起こす編集者です。\n'
    + '次の記録を読み、同じ種類の作業を毎回同じ手順で回すための**作成指示**を書いてください。\n'
    + 'YAML は書かないでください（実際の定義は statemachine-use スキルが作ります）。\n\n'
    + `規則:\n${DISTILL_RULES}\n`
    + '- 形式: {"name":"業務名","machine":"英小文字とハイフンの識別名",'
    + '"instruction":"手順の指示文（Markdown可）"}\n'
    + '- instruction は次を順に含める:\n'
    + '  1. この業務の目的（1〜2 文）\n'
    + '  2. 工程の並び。**1 工程 1 成果物**にする（成果物が 2 つあるなら工程を割る）\n'
    + '  3. 各工程の成功をどう確かめるか。コマンドで測れるなら、そのコマンドを書く\n'
    + '  4. 分岐がある場合は「どの出力なら次へ進み、どの出力ならやり直すか」\n'
    + '  5. 終了条件（どうなったら完了か）\n'
    + '- 会話の経緯・その場限りの相談は書かない。毎回同じように読める手順にする。\n\n'
    + (String(hint || '').trim() ? `利用者の補足:\n${String(hint).trim()}\n\n` : '')
    + `記録:\n${body}`;
}

function normalizeStatemachineDraft(obj, { name: fallbackName } = {}) {
  const instruction = String((obj && obj.instruction) || '').trim();
  if (!instruction) throw new Error('エージェントの応答に手順がありません');
  const name = String((obj && obj.name) || '').trim() || String(fallbackName || '').trim() || '定型業務';
  return {
    kind: 'statemachine',
    name,
    machine: sanitizeMachineId((obj && obj.machine) || name, `routine-${Date.now()}`),
    instruction,
    parameters: templateParameters.inputParameterKeys(instruction),
  };
}

function normalizeRequestDraft(obj) {
  const request = String((obj && obj.request) || '').trim();
  if (!request) throw new Error('エージェントの応答に依頼文がありません');
  return {
    kind: 'request',
    title: String((obj && obj.title) || '').trim(),
    request,
    parameters: templateParameters.inputParameterKeys(request),
  };
}

// 下書きの工程グラフを保存形の形へ寄せる。id・依存・tier の最終検証は
// adhoc.normalizeWorkflow が行う（検証を 2 実装にしない）。
function normalizeWorkflowDraft(obj, { id, tier }) {
  const rawNodes = Array.isArray(obj && obj.nodes) ? obj.nodes : [];
  if (!rawNodes.length) throw new Error('エージェントの応答に工程がありません');
  const seen = new Set();
  const nodes = rawNodes.map((node, index) => {
    const raw = String((node && node.id) || '').trim().toLowerCase().replace(/[^a-z0-9-]+/g, '-')
      .replace(/^-+|-+$/g, '');
    const nodeId = raw && !seen.has(raw) ? raw : `step-${index + 1}`;
    seen.add(nodeId);
    const kind = adhoc.NODE_KINDS.includes(String((node && node.kind) || '')) && node.kind !== 'human'
      ? String(node.kind) : 'work';
    return {
      id: nodeId,
      label: String((node && node.label) || nodeId).trim() || nodeId,
      goal: String((node && node.goal) || '').trim(),
      kind,
      tier,
      deps: (Array.isArray(node && node.deps) ? node.deps : []).map((dep) => String(dep || '').trim()
        .toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '')).filter(Boolean),
      x: 60 + index * 240,
      y: 80,
    };
  });
  // 応答が実在しない工程を指していたら落とす（未知依存はグラフ検証で弾かれるため）。
  const ids = new Set(nodes.map((node) => node.id));
  for (const node of nodes) node.deps = [...new Set(node.deps.filter((dep) => ids.has(dep) && dep !== node.id))];
  const request = String((obj && obj.request) || '').trim();
  return {
    kind: 'workflow',
    request,
    // version は書かない。entry / exit は保存時に normalizeWorkflow がルート / 末端から
    // 導出する（下書きの段階で明示宣言を持たせると、工程を足すたびに手で直すことになる）。
    workflow: {
      id,
      name: String((obj && obj.name) || '').trim() || '流用フロー',
      description: String((obj && obj.description) || '').trim(),
      purpose: 'implementation',
      nodes,
    },
    parameters: templateParameters.inputParameterKeys(request, ...nodes.map((node) => node.goal)),
  };
}

// 下書きの実行レベルは「自動（実行方針を継承）」に置く。どの段で回すかは端末の実行方針が
// 決めるべきもので、会話の記録から推測させる材料ではない（人が編集画面で締められる）。
const DRAFT_TIER = 'auto';

function draftWorkflowId(prefix) {
  const stamp = new Date().toISOString().replace(/[^0-9]/g, '').slice(0, 14);
  return `${prefix}-${stamp}`;
}

// セッションを種に AI 下書きを作る（確定は人＝C4）。transcript はここで読むだけで、
// 戻り値にも本文は入れない——画面へ返すのは下書きと来歴だけ。
async function distillSession(config, { cli, sessionId, kind, hint, cwd } = {}, runShell) {
  const agentCliName = String(cli || '').trim();
  const nativeId = String(sessionId || '').trim();
  if (!agentCliName || !nativeId) throw new Error('セッションを選んでください');
  const found = await audit.sessions(config,
    { cli: agentCliName, nativeId, limit: 1 }, ...(runShell ? [runShell] : []));
  const session = ((found && found.sessions) || [])[0];
  if (!session) throw new Error('この端末にそのセッションの記録がありません');
  const body = transcriptDigest(session);
  if (!body) {
    // with_transcripts が無効な端末（メタデータだけ）は下書きを作れない。空フォームへ縮退する。
    throw new Error('この端末には会話の本文が保存されていません（agent-audit の with_transcripts が無効です）');
  }
  const wanted = draftKind(kind);
  const resolved = agent.resolveDashboardAgent(config, cwd, { purpose: 'session-distill' });
  const prompts = {
    workflow: () => workflowDraftPrompt(body, hint, adhoc.NODE_KINDS.filter((item) => item !== 'human')),
    statemachine: () => statemachineDraftPrompt(body, hint),
    request: () => requestDraftPrompt(body, hint),
  };
  const raw = await agent.runDashboardAgent(config, resolved, 'session-distill',
    () => agent.runAgent(resolved, prompts[wanted](), cwd));
  const obj = agent.extractJson(raw);
  if (!obj) throw new Error(`エージェントの応答からJSONを取り出せませんでした: ${String(raw).slice(0, 120)}…`);
  const drafts = {
    workflow: () => normalizeWorkflowDraft(obj, { id: draftWorkflowId('session'), tier: DRAFT_TIER }),
    statemachine: () => normalizeStatemachineDraft(obj),
    request: () => normalizeRequestDraft(obj),
  };
  const draft = drafts[wanted]();
  const source = adhoc.workflowSourceFromSession(agentCliName, nativeId);
  return {
    draft: { ...draft, source },
    source,
    cli: resolved.cli,
    model: resolved.model,
    // 来歴の表示材料。本文（messages）は返さない。
    session: {
      agent_cli: agentCliName,
      native_id: nativeId,
      cwd: String(session.cwd || ''),
      turns: Number(session.turns) || 0,
      model: String(session.model || ''),
      created_at: session.created_at,
      updated_at: session.updated_at,
    },
  };
}

// 過去 run を種にする。入力（要求文・plan）は inbox 記録にそのまま残っているので
// LLM は使わない——決定的に写せるものを推測させない。
function distillRun(config, { runId, kind } = {}) {
  const id = String(runId || '').trim();
  if (!id || path.basename(id) !== id) throw new Error(`不正な run ID です: ${runId}`);
  const record = adhoc.readInbox(adhoc.resolveBusDir(config), id);
  if (!record) throw new Error(`inbox 記録が見つかりません: ${id}`);
  const source = adhoc.workflowSourceFromRun(id);
  const request = String(record.request || '').trim();
  const plan = record.plan && Array.isArray(record.plan.nodes) ? record.plan : null;
  // 定型業務（statemachine）への蒸留は素の CLI セッションだけの経路にする。run は
  // 既に工程グラフを持っているので、繰り返しはフォーク・一括投函・保存形テンプレートで
  // 足りる——同じ仕事の入口を 2 つ作らない（C7）。
  if (draftKind(kind) === 'workflow') {
    if (!plan) throw new Error('この run は工程グラフを持たないため、フローとして保存できません');
    const nodes = plan.nodes.filter((node) => node && node.kind !== 'human').map((node, index) => ({
      id: String(node.id),
      label: String(node.id),
      goal: String(node.goal || ''),
      kind: String(node.kind || 'work'),
      tier: String(node.tier || '') || DRAFT_TIER,
      deps: (Array.isArray(node.deps) ? node.deps : []).map(String),
      x: 60 + index * 240,
      y: 80,
    }));
    const ids = new Set(nodes.map((node) => node.id));
    for (const node of nodes) node.deps = node.deps.filter((dep) => ids.has(dep));
    return {
      draft: {
        kind: 'workflow',
        request,
        source,
        parameters: templateParameters.inputParameterKeys(request, ...nodes.map((node) => node.goal)),
        workflow: {
          id: draftWorkflowId('run'),
          name: String(record.title || plan.name || `run ${id}`).slice(0, 60),
          description: `run ${id} の入力から起こしたフロー`,
          purpose: String(record.purpose || 'implementation') === 'design' ? 'design' : 'implementation',
          nodes,
        },
      },
      source,
    };
  }
  return {
    draft: {
      kind: 'request',
      title: String(record.title || '').trim(),
      request,
      source,
      parameters: templateParameters.inputParameterKeys(request),
    },
    source,
  };
}

// 確定した蒸留物をワークフローライブラリ（ユーザー共通 `~/.agents/workflows/`）へ保存する。
// 登録リポジトリ共有版・同梱版は読み取り専用なので、共有は通常の Git 運用が配る
// （agent-dashboard の保存・削除制約）。
function saveDistilled(config, { workflow, source } = {}) {
  if (!workflow || typeof workflow !== 'object') throw new Error('保存するフローがありません');
  const provenance = String(source || workflow.source || '').trim();
  return adhoc.saveWorkflow(config, {
    ...workflow,
    ...(provenance ? { source: provenance } : {}),
  });
}

// --- バッチ投函（§5: リポジトリをまたぐ map） --------------------------------

function normalizeBatchRows(rows, keys) {
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) throw new Error('パラメータ行を1件以上入力してください');
  if (list.length > BATCH_MAX_ROWS) {
    throw new Error(`一括投函は ${BATCH_MAX_ROWS} 件までです（指定 ${list.length} 件）`);
  }
  const wanted = Array.isArray(keys) ? keys.map(String) : [];
  return list.map((row, index) => {
    const raw = row && typeof row === 'object' ? row : {};
    const parameters = raw.parameters && typeof raw.parameters === 'object' ? raw.parameters : {};
    let values;
    try {
      values = templateParameters.validateParameters({ keys: wanted, error: '' }, parameters);
    } catch (error) {
      throw new Error(`${index + 1} 行目: ${error.message}`, { cause: error });
    }
    return {
      index,
      cwd: String(raw.cwd || '').trim(),
      title: String(raw.title || '').trim(),
      parameters: values,
    };
  });
}

// 「n 件 × 概算予算」の材料。実測（agent-budget の台帳）があればそれを、無ければ
// 既定の桁を使い、どちらなのかを `measured` で言い切る（数字の出どころを隠さない）。
function batchEstimate(config, count) {
  const workload = (() => {
    try {
      return (budget.usage(config).workloads || {}).flow || {};
    } catch {
      // 台帳がまだ無い端末でも件数の確認はできる。実測が無いことは measured で示す。
      return {};
    }
  })();
  const runs = Number(workload.recordCount) || 0;
  const used = Number(workload.totalTokens) || 0;
  const measured = runs > 0 && used > 0;
  const perRun = measured ? Math.round(used / runs) : FALLBACK_TOKENS_PER_RUN;
  const cap = Number(workload.tokenCap) || 0;
  const estimated = perRun * count;
  return {
    count,
    measured,
    sampleRuns: runs,
    perRunTokens: perRun,
    estimatedTokens: estimated,
    usedTokens: used,
    tokenCap: cap,
    remainingTokens: cap > 0 ? Math.max(0, cap - used) : 0,
    // 上限を超える見込みなら投函前に言う。止めるのは常駐体（node-budget）だが、
    // 「投げたのに動かない」を人が投函前に知れるようにする。
    exceeds: cap > 0 && used + estimated > cap,
    maxRows: BATCH_MAX_ROWS,
  };
}

// 投函前の確認材料。行ごとの書込先と、この端末が担当宣言していないリポジトリを示す。
function batchPreview(config, { rows, parameterKeys } = {}) {
  const clean = normalizeBatchRows(rows, parameterKeys);
  const projects = adhoc.listProjects(config).map((project) => String(project.dir));
  const declared = new Set(projects.map((dir) => path.resolve(dir)));
  return {
    rows: clean.map((row) => {
      const root = row.cwd ? adhoc.repositoryRoot(row.cwd) : '';
      return {
        ...row,
        repositoryRoot: root,
        // 担当宣言の外にあるリポジトリは、この端末で実行する代わりに委譲公示板
        // （agent-board）へ流せる。判断は人がするので、ここでは印を付けるだけ。
        unregistered: !!root && !declared.has(path.resolve(root)),
      };
    }),
    estimate: batchEstimate(config, clean.length),
  };
}

function newBatchId() {
  const t = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `batch-${t.getFullYear()}${pad(t.getMonth() + 1)}${pad(t.getDate())}`
    + `-${pad(t.getHours())}${pad(t.getMinutes())}${pad(t.getSeconds())}`
    + `-${Math.floor(1000 + Math.random() * 9000)}`;
}

// n 本の adhoc run を 1 つの batch_id で投函する。各 run は自分の workspace を持つので
// 「1 run = 1 workspace」は崩さない。1 件でも失敗したらそこで止める——同じ原因で n 件
// 失敗させても人が読む情報は増えない（止まることを先に約束する。C1）。
function batchSubmit(config, payload = {}) {
  const { rows, parameterKeys, confirmed, ...base } = payload;
  const clean = normalizeBatchRows(rows, parameterKeys);
  if (confirmed !== true) {
    throw new Error('件数と概算予算の確認が済んでいません');
  }
  const batchId = newBatchId();
  const submitted = [];
  for (const row of clean) {
    try {
      const result = adhoc.submit(config, {
        ...base,
        ...(row.cwd ? { cwd: row.cwd } : {}),
        ...(row.title ? { title: row.title } : {}),
        parameters: row.parameters,
        batchId,
      });
      submitted.push({ index: row.index, runId: result.runId, cwd: row.cwd, title: row.title });
    } catch (error) {
      return {
        batchId,
        submitted,
        failed: { index: row.index, cwd: row.cwd, error: String((error && error.message) || error) },
      };
    }
  }
  return { batchId, submitted, failed: null };
}

module.exports = {
  BATCH_MAX_ROWS,
  FALLBACK_TOKENS_PER_RUN,
  DRAFT_KINDS,
  draftKind,
  transcriptDigest,
  requestDraftPrompt,
  workflowDraftPrompt,
  statemachineDraftPrompt,
  sanitizeMachineId,
  normalizeRequestDraft,
  normalizeWorkflowDraft,
  normalizeStatemachineDraft,
  distillSession,
  distillRun,
  saveDistilled,
  normalizeBatchRows,
  batchEstimate,
  batchPreview,
  batchSubmit,
};
