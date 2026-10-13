// 正規化結果を Markdown にする。ここに書くのは API 由来の事実だけで、設計判断は書かない。

import { REASONS } from './normalize.mjs';

const MAX_TREE_LINES = 400;

function size(box) {
  return box ? `${box.width}×${box.height}` : 'サイズ未取得';
}

function layoutSummary(layout) {
  if (!layout) return '';
  const parts = [];
  if (layout.mode && layout.mode !== 'NONE') {
    parts.push(layout.mode === 'HORIZONTAL' ? '横並び' : layout.mode === 'VERTICAL' ? '縦並び' : layout.mode === 'GRID' ? 'グリッド' : layout.mode);
    if (layout.gap !== undefined) parts.push(`gap ${layout.gap}`);
    if (layout.padding) {
      const p = layout.padding;
      parts.push(`padding ${[p.top, p.right, p.bottom, p.left].map((v) => (v === undefined ? '-' : v)).join(' ')}`);
    }
    if (layout.primaryAxisAlign) parts.push(`主軸 ${layout.primaryAxisAlign}`);
    if (layout.counterAxisAlign) parts.push(`交差軸 ${layout.counterAxisAlign}`);
    if (layout.wrap === 'WRAP') parts.push('折り返し');
  }
  if (layout.self?.layoutSizingHorizontal || layout.self?.layoutSizingVertical) {
    parts.push(`幅 ${layout.self.layoutSizingHorizontal || '-'} / 高さ ${layout.self.layoutSizingVertical || '-'}`);
  }
  if (layout.self?.layoutPositioning === 'ABSOLUTE') parts.push('絶対配置');
  return parts.join(' / ');
}

function textSummary(node) {
  const s = node.text?.style || {};
  const font = [s.fontFamily, s.fontWeight, s.fontSize && `${s.fontSize}px`, s.lineHeightPx && `行高 ${round1(s.lineHeightPx)}px`].filter(Boolean).join(' ');
  const chars = (node.text?.characters ?? '').replace(/\s+/g, ' ');
  const shown = chars.length > 60 ? `${chars.slice(0, 60)}…` : chars;
  return `「${shown}」${font ? ` ${font}` : ''}`;
}

function round1(n) {
  return Math.round(n * 10) / 10;
}

export function renderTree(root, { maxLines = MAX_TREE_LINES } = {}) {
  const lines = [];
  let truncated = 0;
  const walk = (n, depth) => {
    if (lines.length >= maxLines) {
      truncated += 1;
      (n.children || []).forEach((c) => walk(c, depth + 1));
      return;
    }
    const bits = [`[${n.type}] ${n.name} (${n.id})`];
    if (n.box) bits.push(size(n.box));
    if (n.visible === false) bits.push('非表示');
    if (n.type === 'TEXT') bits.push(textSummary(n));
    if (n.component?.name) bits.push(`→ ${n.component.setName ? `${n.component.setName} / ` : ''}${n.component.name}`);
    if (n.codeMapping?.component) bits.push(`コード対応: ${n.codeMapping.component}`);
    const l = layoutSummary(n.layout);
    if (l) bits.push(l);
    if (n.childrenStatus === 'not_fetched') bits.push('子は未取得');
    lines.push(`${'  '.repeat(depth)}- ${bits.join(' — ')}`);
    (n.children || []).forEach((c) => walk(c, depth + 1));
  };
  walk(root, 0);
  if (truncated) lines.push(`- …ほか ${truncated} ノード（normalized の JSON を参照）`);
  return lines.join('\n');
}

function table(headers, rows) {
  if (!rows.length) return '（なし）';
  const esc = (v) => String(v ?? '').replace(/\|/g, '\\|').replace(/\n/g, ' ');
  return [`| ${headers.join(' | ')} |`, `| ${headers.map(() => '---').join(' | ')} |`, ...rows.map((r) => `| ${r.map(esc).join(' | ')} |`)].join('\n');
}

const join = (v) => (Array.isArray(v) ? v.join(', ') : v instanceof Set ? [...v].join(', ') : v ?? '');

/** 1 つの対象ノードの Markdown（normalized/<id>.md）。 */
export function renderNodeMarkdown(result, info) {
  const { root, facts, missing } = result;
  const out = [];
  out.push(`# ${root.name}（${root.type} / ${root.id}）`, '');
  out.push(`- ファイル: ${info.fileName ?? '不明'}（file key ${info.fileKey}）`);
  out.push(`- version: ${info.version ?? '応答になし'} / 最終更新: ${info.lastModified ?? '応答になし'} / 取得: ${info.fetchedAt}${info.fromCache ? '（キャッシュ）' : ''}`);
  out.push(`- サイズ: ${size(root.box)} / ノード数: ${result.stats.nodes}${info.depth != null ? ` / depth ${info.depth}` : ''}`);
  out.push(`- 生データ: ${info.rawPath} / 正規化 JSON: ${info.jsonPath}`, '');
  out.push('この文書は Figma API の応答から機械的に作った事実だけを載せる。値が無い項目は応答に無かったもので、既定値で補っていない。', '');
  out.push('## 構造', '', renderTree(root), '');
  out.push('## テキスト', '', table(['ID', 'レイヤー', '文字列'], facts.texts.slice(0, 200).map((t) => [t.id, t.hidden ? `${t.name}（非表示）` : t.name, t.characters])), '');
  out.push('## 色（単色の塗り・線）', '', table(['色', '不透明度', '用途', 'スタイル名', '使用数'], facts.colors.map((c) => [c.color, c.alpha, join(c.uses), join(c.styles), c.count])), '');
  out.push('## タイポグラフィ', '', table(['フォント', 'ウェイト', 'サイズ', '行高(px)', '字間', 'スタイル名', '使用数'], facts.typography.map((t) => [t.fontFamily, t.fontWeight, t.fontSize, t.lineHeightPx != null ? round1(t.lineHeightPx) : '', t.letterSpacing, t.style, t.count])), '');
  out.push('## 使われているコンポーネント', '', table(['コンポーネント', 'バリアント', 'インスタンス数', 'ライブラリ', 'コード対応'], facts.components.map((c) => [c.name, join(c.variants), c.instances.length, c.remote ? '外部' : 'このファイル', c.codeMapping ? `${c.codeMapping.component}（${c.codeMapping.source}）` : '未対応付け'])), '');
  out.push('## 画像の塗り（imageRef）', '', table(['imageRef', 'ノード', '配置'], facts.imageFills.map((f) => [f.imageRef, join(f.nodeIds), f.scaleMode])), '');
  out.push('## SVG 書き出しの候補（ベクターだけでできたノード）', '', table(['ID', '名前', '種類', '場所'], facts.svgCandidates.slice(0, 100).map((s) => [s.id, s.name, s.type, s.hidden ? `${s.path}（非表示）` : s.path])), '');
  out.push('## 変数のバインド', '', table(['変数 ID', '名前', '型', '値（モード別）', '使われる属性'], facts.variables.map((v) => [v.variableId, v.name ?? '未解決', v.resolvedType ?? '', v.valuesByMode ? JSON.stringify(v.valuesByMode) : '', join(v.fields)])), '');
  out.push('## プロトタイプの操作・遷移', '', table(['ノード', 'トリガー', '動作', '遷移先'], facts.interactions.flatMap((i) => i.interactions.flatMap((it) => it.actions.map((a) => [`${i.name} (${i.id})`, it.trigger?.type ?? '', a.type ?? '', a.destinationName ? `${a.destinationName} (${a.destinationId})` : a.destinationId ? `${a.destinationId}（${a.destinationNote ?? ''}）` : a.url ?? ''])))), '');
  out.push('## 注釈', '', table(['ノード', '注釈'], facts.annotations.map((a) => [`${a.name} (${a.id})`, a.annotations.map((x) => x.label || x.labelMarkdown || JSON.stringify(x.properties || {})).join(' / ')])), '');
  out.push(`## 不足情報`, '', missing.length ? `${missing.length} 件（理由ごとの内訳は missing-information.md、全件は正規化 JSON の missing）` : '（なし）', '');
  return `${out.join('\n')}\n`;
}

/** export の design-summary.md。事実と判断を分ける。 */
export function renderDesignSummary(manifest, results) {
  const out = [];
  out.push(`# デザイン要約: ${manifest.file.name ?? manifest.source.fileKey}`, '');
  out.push('## 取得情報', '');
  out.push(`- Figma: ${manifest.source.url ?? manifest.source.fileKey}`);
  out.push(`- version: ${manifest.file.version ?? '応答になし'} / 最終更新: ${manifest.file.lastModified ?? '応答になし'}`);
  out.push(`- 取得: ${manifest.generatedAt}${manifest.cache.used ? '（一部キャッシュ。--refresh で再取得）' : ''}`);
  out.push(`- 状態: ${manifest.status === 'complete' ? 'すべて取得' : '一部失敗（missing-information.md を参照）'}`, '');
  out.push('## 事実（Figma API 由来）', '');
  out.push('各画面・部品の詳細は「詳細」の Markdown を読む。大きな JSON は必要な部分だけ開く。', '');
  for (const r of results) {
    const a = manifest.artifacts.byNode[r.nodeId] || {};
    out.push(`### ${r.root.name}（${r.root.type} / ${r.nodeId}）`, '');
    out.push(`- サイズ: ${size(r.root.box)} / ノード数 ${r.stats.nodes} / 直下の子 ${r.root.children?.length ?? 0}`);
    if (r.root.layout) out.push(`- レイアウト: ${layoutSummary(r.root.layout) || 'Auto Layout なし'}`);
    out.push(`- テキスト ${r.facts.texts.length} 件 / 色 ${r.facts.colors.length} 種 / 文字スタイル ${r.facts.typography.length} 種 / 画像 ${r.facts.imageFills.length} 件 / SVG 候補 ${r.facts.svgCandidates.length} 件`);
    if (r.facts.components.length) {
      out.push(`- コンポーネント: ${r.facts.components.slice(0, 10).map((c) => `${c.name}×${c.instances.length}${c.codeMapping ? `（→ ${c.codeMapping.component}）` : ''}`).join('、')}`);
    }
    if (r.facts.interactions.length) out.push(`- プロトタイプの操作: ${r.facts.interactions.length} ノード`);
    if (r.facts.annotations.length) out.push(`- 注釈: ${r.facts.annotations.length} 件`);
    out.push(`- 不足情報: ${r.missing.length} 件`);
    out.push(`- 詳細: ${a.markdown ?? '-'} / JSON: ${a.normalized ?? '-'} / 生データ: ${a.raw ?? '-'}`);
    out.push(`- スクリーンショット: ${a.screenshot ?? '未取得'}`, '');
  }
  if (manifest.artifacts.assets.length) {
    out.push('## 素材', '', `${manifest.artifacts.assets.length} 件。node ID・imageRef と保存先の対応は ${manifest.artifacts.assetsMap} を参照。`, '');
  }
  out.push('## 判断（このファイルには書かない）', '');
  out.push('既存部品への対応付け・コンポーネント分割・Props・状態・Hooks の設計はこのスキルの範囲外。後工程の設計書に書き、ここに書いた事実と混ぜない。', '');
  return `${out.join('\n')}\n`;
}

const NOT_IN_API = [
  '操作したときの振る舞い（入力検証、エラー表示、読み込み中、空の状態）。プロトタイプの遷移以外は API に無い',
  '画面が呼ぶ API・データの形・権限',
  'レスポンシブの切り替え条件（別フレームで描かれていても、どの幅で切り替えるかは API に無い）',
  'アクセシビリティ（読み上げラベル・フォーカス順）。注釈に書かれていれば上の注釈欄に出る',
  'Code Connect の対応（REST API では取れない。--mapping / --code-connect-dir で渡せる）',
];

/** missing-information.md。理由ごとに分ける。 */
export function renderMissing(manifest, results) {
  const out = [];
  out.push(`# 不足情報: ${manifest.file.name ?? manifest.source.fileKey}`, '');
  out.push('理由の意味: 未取得 = 取得範囲（depth・対象ノード）の外 / 属性なし = API の応答に属性が無かった / アクセス不可 = 権限・プランで取れなかった / 未解決 = ID はあるが中身を引けなかった / 取得失敗 = API 呼び出しやダウンロードが失敗した。', '');
  if (manifest.failures.length) {
    out.push('## 取得失敗（この成果物は一部欠けている）', '');
    out.push(table(['対象', '内容'], manifest.failures.map((f) => [f.target, f.message])), '');
  }
  const all = results.flatMap((r) => r.missing.map((m) => ({ ...m, target: r.nodeId })));
  for (const reason of ['inaccessible', 'unresolved', 'not_fetched', 'absent']) {
    const items = all.filter((m) => m.reason === reason);
    if (!items.length) continue;
    out.push(`## ${REASONS[reason]}（${items.length} 件）`, '');
    const shown = items.slice(0, 200);
    out.push(table(['ノード', '属性', '内容'], shown.map((m) => [`${m.nodeName} (${m.nodeId})`, m.field, m.detail])));
    if (items.length > shown.length) out.push('', `…ほか ${items.length - shown.length} 件（正規化 JSON の missing を参照）`);
    out.push('');
  }
  if (!all.length && !manifest.failures.length) out.push('API から取れる範囲での欠けはなし。', '');
  out.push('## API では取れない情報（後工程で確認する）', '', ...NOT_IN_API.map((s) => `- ${s}`), '');
  return `${out.join('\n')}\n`;
}

/** inspect の一覧（ページ・フレーム）。 */
export function renderInspect(rootNodes, info) {
  const out = [];
  out.push(`# ${info.fileName ?? info.fileKey}`, '');
  out.push(`- file key: ${info.fileKey} / version: ${info.version ?? '応答になし'} / 最終更新: ${info.lastModified ?? '応答になし'} / 取得: ${info.fetchedAt}${info.fromCache ? '（キャッシュ）' : ''}`);
  out.push(`- depth: ${info.depth}（より深く見るには --depth を増やすか、node を指定して context）`, '');
  for (const r of rootNodes) out.push(renderTree(r, { maxLines: 300 }), '');
  return `${out.join('\n')}\n`;
}
