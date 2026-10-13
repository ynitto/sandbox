// Figma の URL / file key / node ID を解釈する。
//
// 受け付けるのはデザインファイル（/design/・/file/・/proto/）だけ。FigJam・Slides・Make・Sites・
// コミュニティなどは REST API で読める範囲や中身が違うので、理由を添えて拒否する。

export class InputError extends Error {}

const FIGMA_HOSTS = new Set(['figma.com', 'www.figma.com']);
const DESIGN_KINDS = new Set(['design', 'file', 'proto']);
const REJECTED_KINDS = {
  make: 'Figma Make のファイルは REST API のファイル取得に対応していないため扱えません',
  site: 'Figma Sites のファイルは対象外です',
  sites: 'Figma Sites のファイルは対象外です',
  slides: 'Figma Slides のファイルは対象外です（このスキルは画面デザインのみ扱います）',
  board: 'FigJam ボードは対象外です（このスキルは画面デザインのみ扱います）',
  buzz: 'Figma Buzz のファイルは対象外です',
  community: 'コミュニティのページは対象外です。複製して自分のファイルにした URL を渡してください',
  files: 'ファイル一覧のページです。対象ファイルを開いた URL を渡してください',
};

const FILE_KEY_RE = /^[A-Za-z0-9]{10,64}$/;
// 1:2 / 1-2 / I1:2;3:4（インスタンス内のノード）を受ける。
const NODE_ID_RE = /^I?\d+[:-]\d+(?:;I?\d+[:-]\d+)*$/;

/** node-id のハイフン表記・URL エンコードをコロン表記にそろえる。 */
export function normalizeNodeId(raw) {
  if (typeof raw !== 'string') throw new InputError('node ID が空です');
  let value = raw.trim();
  try {
    value = decodeURIComponent(value);
  } catch {
    throw new InputError(`node ID の URL エンコードが壊れています: ${raw}`);
  }
  if (!NODE_ID_RE.test(value)) {
    throw new InputError(`node ID の形式が不正です: ${raw}（例: 12:34 または 12-34）`);
  }
  return value.replace(/-/g, ':');
}

/** カンマ区切り・複数指定の node ID をそろえて重複を除く。 */
export function normalizeNodeIds(values) {
  const out = [];
  for (const v of values || []) {
    for (const part of String(v).split(',')) {
      if (!part.trim()) continue;
      const id = normalizeNodeId(part);
      if (!out.includes(id)) out.push(id);
    }
  }
  return out;
}

/**
 * Figma URL か file key を受け取り、{ fileKey, mainFileKey, branchKey, nodeIds, kind } を返す。
 * branch の URL では API に渡す fileKey は branch key になる（公式: :key は branch key も可）。
 */
export function parseFigmaInput(input) {
  if (typeof input !== 'string' || !input.trim()) {
    throw new InputError('Figma の URL か file key を指定してください');
  }
  const text = input.trim();
  if (FILE_KEY_RE.test(text)) {
    return { fileKey: text, mainFileKey: text, branchKey: null, nodeIds: [], kind: 'key' };
  }
  let url;
  try {
    url = new URL(text);
  } catch {
    throw new InputError(`Figma の URL でも file key でもありません: ${text}`);
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') {
    throw new InputError(`http(s) の URL ではありません: ${url.protocol}`);
  }
  if (!FIGMA_HOSTS.has(url.hostname.toLowerCase())) {
    throw new InputError(`figma.com の URL ではありません: ${url.hostname}`);
  }
  const segments = url.pathname.split('/').filter(Boolean).map((s) => {
    try {
      return decodeURIComponent(s);
    } catch {
      return s;
    }
  });
  const kind = (segments[0] || '').toLowerCase();
  if (REJECTED_KINDS[kind]) throw new InputError(REJECTED_KINDS[kind]);
  if (!DESIGN_KINDS.has(kind)) {
    throw new InputError(`対応していない Figma の URL です（/design/・/file/・/proto/ のみ）: ${url.pathname}`);
  }
  const mainFileKey = segments[1];
  if (!mainFileKey || !FILE_KEY_RE.test(mainFileKey)) {
    throw new InputError(`URL から file key を読み取れません: ${url.pathname}`);
  }
  let branchKey = null;
  if ((segments[2] || '').toLowerCase() === 'branch') {
    branchKey = segments[3];
    if (!branchKey || !FILE_KEY_RE.test(branchKey)) {
      throw new InputError(`URL から branch key を読み取れません: ${url.pathname}`);
    }
  }
  const nodeIds = [];
  const nodeParam = url.searchParams.get('node-id');
  if (nodeParam) nodeIds.push(normalizeNodeId(nodeParam));
  return { fileKey: branchKey || mainFileKey, mainFileKey, branchKey, nodeIds, kind };
}

/** 成果物に残すため、URL から問い合わせ以外の余計な部分（t= などの共有トークン）を落とす。 */
export function sanitizeSourceUrl(input) {
  try {
    const url = new URL(input);
    const keep = new URL(`${url.protocol}//${url.host}${url.pathname}`);
    const nodeId = url.searchParams.get('node-id');
    if (nodeId) keep.searchParams.set('node-id', nodeId);
    return keep.toString();
  } catch {
    return input;
  }
}
