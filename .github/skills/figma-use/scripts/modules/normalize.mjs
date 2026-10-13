// GET /v1/files/:key/nodes の 1 ノード分を、エージェントが読みやすい形にそろえる。
//
// 方針:
// - API の応答に無い属性は補わない（0 や空文字を入れない）。正規化 JSON から省く。
// - 「未取得」「属性なし」「アクセス不可」「未解決」は missing に理由付きで残す。
// - 元の値を追えるよう、各ノードに id を残し、raw/ の生 JSON と同じ ID で引ける。

export const REASONS = {
  not_fetched: '未取得',
  absent: '属性なし',
  inaccessible: 'アクセス不可',
  unresolved: '未解決',
  failed: '取得失敗',
};

const CONTAINER_TYPES = new Set([
  'DOCUMENT', 'CANVAS', 'FRAME', 'GROUP', 'SECTION', 'COMPONENT', 'COMPONENT_SET', 'INSTANCE',
  'BOOLEAN_OPERATION', 'TRANSFORM_GROUP', 'TABLE',
]);
const VECTOR_TYPES = new Set(['VECTOR', 'BOOLEAN_OPERATION', 'STAR', 'LINE', 'ELLIPSE', 'REGULAR_POLYGON', 'RECTANGLE']);

export function colorToHex(color) {
  if (!color || typeof color.r !== 'number') return null;
  const h = (v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, '0');
  return `#${h(color.r)}${h(color.g)}${h(color.b)}`.toUpperCase();
}

function pick(obj, keys) {
  const out = {};
  for (const k of keys) if (obj[k] !== undefined) out[k] = obj[k];
  return Object.keys(out).length ? out : undefined;
}

function round(n) {
  return typeof n === 'number' ? Math.round(n * 100) / 100 : n;
}

function normalizePaint(p) {
  const out = { type: p.type };
  if (p.visible === false) out.visible = false;
  if (p.opacity !== undefined) out.opacity = round(p.opacity);
  if (p.color) {
    out.color = colorToHex(p.color);
    if (p.color.a !== undefined && p.color.a !== 1) out.alpha = round(p.color.a);
  }
  if (p.blendMode && p.blendMode !== 'NORMAL') out.blendMode = p.blendMode;
  if (p.gradientStops) {
    out.gradientStops = p.gradientStops.map((s) => ({ position: round(s.position), color: colorToHex(s.color), alpha: round(s.color?.a) }));
  }
  if (p.imageRef) out.imageRef = p.imageRef;
  if (p.gifRef) out.gifRef = p.gifRef;
  if (p.scaleMode) out.scaleMode = p.scaleMode;
  if (p.boundVariables) out.boundVariables = p.boundVariables;
  return out;
}

function normalizeEffect(e) {
  const out = { type: e.type };
  if (e.visible === false) out.visible = false;
  if (e.radius !== undefined) out.radius = round(e.radius);
  if (e.color) {
    out.color = colorToHex(e.color);
    if (e.color.a !== undefined) out.alpha = round(e.color.a);
  }
  if (e.offset) out.offset = { x: round(e.offset.x), y: round(e.offset.y) };
  if (e.spread !== undefined) out.spread = round(e.spread);
  if (e.blendMode && e.blendMode !== 'NORMAL') out.blendMode = e.blendMode;
  return out;
}

function normalizeLayout(n) {
  const layout = {};
  if (n.layoutMode !== undefined) layout.mode = n.layoutMode;
  if (n.layoutWrap !== undefined) layout.wrap = n.layoutWrap;
  if (n.primaryAxisAlignItems !== undefined) layout.primaryAxisAlign = n.primaryAxisAlignItems;
  if (n.counterAxisAlignItems !== undefined) layout.counterAxisAlign = n.counterAxisAlignItems;
  if (n.counterAxisAlignContent !== undefined) layout.counterAxisAlignContent = n.counterAxisAlignContent;
  if (n.primaryAxisSizingMode !== undefined) layout.primaryAxisSizingMode = n.primaryAxisSizingMode;
  if (n.counterAxisSizingMode !== undefined) layout.counterAxisSizingMode = n.counterAxisSizingMode;
  const padding = pick(n, ['paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft']);
  if (padding) {
    layout.padding = {};
    for (const [k, v] of Object.entries(padding)) layout.padding[k.replace('padding', '').toLowerCase()] = v;
  }
  if (n.itemSpacing !== undefined) layout.gap = n.itemSpacing;
  if (n.counterAxisSpacing !== undefined) layout.counterAxisGap = n.counterAxisSpacing;
  if (n.itemReverseZIndex !== undefined) layout.itemReverseZIndex = n.itemReverseZIndex;
  if (n.strokesIncludedInLayout !== undefined) layout.strokesIncludedInLayout = n.strokesIncludedInLayout;
  const grid = pick(n, ['gridRowCount', 'gridColumnCount', 'gridRowGap', 'gridColumnGap', 'gridColumnsSizing', 'gridRowsSizing']);
  if (grid) layout.grid = grid;
  // 親の Auto Layout の中での振る舞い。
  const child = pick(n, ['layoutSizingHorizontal', 'layoutSizingVertical', 'layoutAlign', 'layoutGrow', 'layoutPositioning', 'minWidth', 'maxWidth', 'minHeight', 'maxHeight']);
  if (child) layout.self = child;
  if (n.constraints) layout.constraints = n.constraints;
  if (n.clipsContent !== undefined) layout.clipsContent = n.clipsContent;
  return Object.keys(layout).length ? layout : undefined;
}

const TEXT_STYLE_KEYS = [
  'fontFamily', 'fontPostScriptName', 'fontStyle', 'fontWeight', 'fontSize', 'italic',
  'lineHeightPx', 'lineHeightUnit', 'lineHeightPercentFontSize', 'letterSpacing',
  'textAlignHorizontal', 'textAlignVertical', 'textCase', 'textDecoration', 'textAutoResize',
  'paragraphSpacing', 'paragraphIndent', 'boundVariables',
];

function normalizeText(n) {
  const text = {};
  if (n.characters !== undefined) text.characters = n.characters;
  if (n.style) text.style = pick(n.style, TEXT_STYLE_KEYS);
  const overrides = n.styleOverrideTable && Object.keys(n.styleOverrideTable).length;
  if (overrides) {
    text.styleOverrides = Object.entries(n.styleOverrideTable).map(([id, s]) => ({ id, ...pick(s, TEXT_STYLE_KEYS) }));
    text.note = '文字ごとにスタイルが変わる箇所がある（characterStyleOverrides は raw/ を参照）';
  }
  if (n.lineTypes && n.lineTypes.some((t) => t !== 'NONE')) text.lineTypes = n.lineTypes;
  return text;
}

/** ノードの各所から変数バインドを集める。 */
export function collectBindings(n) {
  const out = [];
  const visit = (where, bv) => {
    if (!bv || typeof bv !== 'object') return;
    for (const [field, value] of Object.entries(bv)) {
      const list = Array.isArray(value) ? value : [value];
      list.forEach((alias, i) => {
        if (alias && alias.type === 'VARIABLE_ALIAS' && alias.id) {
          out.push({ field: `${where}${field}${Array.isArray(value) ? `[${i}]` : ''}`, variableId: alias.id });
        } else if (alias && typeof alias === 'object' && !alias.type) {
          visit(`${where}${field}.`, alias);
        }
      });
    }
  };
  visit('', n.boundVariables);
  (n.fills || []).forEach((p, i) => visit(`fills[${i}].`, p.boundVariables));
  (n.strokes || []).forEach((p, i) => visit(`strokes[${i}].`, p.boundVariables));
  (n.effects || []).forEach((e, i) => visit(`effects[${i}].`, e.boundVariables));
  if (n.style) visit('style.', n.style.boundVariables);
  for (const [name, prop] of Object.entries(n.componentProperties || {})) visit(`componentProperties.${name}.`, prop.boundVariables);
  return out;
}

function resolveVariable(variableId, variables) {
  if (!variables) return null;
  const v = variables.variables?.[variableId];
  if (!v) return null;
  const collection = variables.variableCollections?.[v.variableCollectionId];
  const modes = Object.fromEntries((collection?.modes || []).map((m) => [m.modeId, m.name]));
  const valuesByMode = {};
  for (const [modeId, value] of Object.entries(v.valuesByMode || {})) {
    const name = modes[modeId] || modeId;
    valuesByMode[name] = value && typeof value === 'object' && 'r' in value
      ? { color: colorToHex(value), alpha: round(value.a) }
      : value;
  }
  return { name: v.name, resolvedType: v.resolvedType, collection: collection?.name, valuesByMode };
}

/**
 * 1 つの対象ノード（nodes 応答の 1 エントリ）を正規化する。
 * entry: { document, components, componentSets, styles }
 * opts: { depth, variables, variablesStatus, mapping }
 */
export function normalizeEntry(nodeId, entry, opts = {}) {
  const ctx = {
    components: entry.components || {},
    componentSets: entry.componentSets || {},
    styles: entry.styles || {},
    depth: opts.depth ?? null,
    variables: opts.variables || null,
    variablesStatus: opts.variablesStatus || 'not_requested',
    mapping: opts.mapping || null,
    missing: [],
    index: new Map(),
    facts: {
      colors: new Map(),
      typography: new Map(),
      texts: [],
      components: new Map(),
      imageFills: new Map(),
      svgCandidates: [],
      interactions: [],
      annotations: [],
      bindings: new Map(),
    },
  };
  const root = normalizeNode(entry.document, ctx, 0, [], [], false);
  for (const it of ctx.facts.interactions) {
    for (const a of it.actions) {
      if (a.destinationId && !a.destinationName) {
        const dest = ctx.index.get(a.destinationId);
        if (dest) a.destinationName = dest.name;
        else a.destinationNote = '遷移先は取得範囲の外（名前は未取得）';
      }
    }
  }
  return {
    nodeId,
    root,
    missing: ctx.missing,
    facts: {
      colors: [...ctx.facts.colors.values()].sort((a, b) => b.count - a.count),
      typography: [...ctx.facts.typography.values()].sort((a, b) => b.count - a.count),
      texts: ctx.facts.texts,
      components: [...ctx.facts.components.values()].sort((a, b) => b.instances.length - a.instances.length),
      imageFills: [...ctx.facts.imageFills.values()],
      // 入れ子の候補は外側だけ残す（アイコンの枠ごと書き出すため）。
      svgCandidates: dropNested(ctx.facts.svgCandidates),
      interactions: ctx.facts.interactions,
      annotations: ctx.facts.annotations,
      variables: [...ctx.facts.bindings.values()],
    },
    stats: { nodes: ctx.index.size },
  };
}

function addMissing(ctx, node, field, reason, detail) {
  ctx.missing.push({ nodeId: node.id, nodeName: node.name, field, reason, detail });
}

function normalizeNode(n, ctx, level, ancestors, ancestorIds, hiddenAncestor) {
  const out = { id: n.id, name: n.name, type: n.type };
  ctx.index.set(n.id, out);
  const pathNames = [...ancestors, n.name];
  const hidden = hiddenAncestor || n.visible === false;
  if (n.visible === false) out.visible = false;
  if (n.locked) out.locked = true;
  if (n.absoluteBoundingBox) {
    const b = n.absoluteBoundingBox;
    out.box = { x: round(b.x), y: round(b.y), width: round(b.width), height: round(b.height) };
  } else if (n.type !== 'DOCUMENT' && n.type !== 'CANVAS') {
    addMissing(ctx, n, 'absoluteBoundingBox', 'absent', 'サイズ・位置が応答にない');
  }
  if (n.rotation) out.rotation = round(n.rotation);
  if (n.opacity !== undefined && n.opacity !== 1) out.opacity = round(n.opacity);
  if (n.blendMode && !['PASS_THROUGH', 'NORMAL'].includes(n.blendMode)) out.blendMode = n.blendMode;
  const layout = normalizeLayout(n);
  if (layout) out.layout = layout;

  if (n.fills?.length) out.fills = n.fills.map(normalizePaint);
  if (n.strokes?.length) {
    out.strokes = n.strokes.map(normalizePaint);
    const stroke = pick(n, ['strokeWeight', 'individualStrokeWeights', 'strokeAlign', 'strokeDashes', 'strokeCap', 'strokeJoin']);
    if (stroke) out.stroke = stroke;
  }
  if (n.cornerRadius !== undefined) out.cornerRadius = n.cornerRadius;
  if (n.rectangleCornerRadii) out.cornerRadii = n.rectangleCornerRadii;
  if (n.effects?.length) out.effects = n.effects.map(normalizeEffect);
  if (n.isMask) out.isMask = true;

  if (n.styles) {
    out.styles = {};
    for (const [role, styleId] of Object.entries(n.styles)) {
      const s = ctx.styles[styleId];
      if (s) out.styles[role] = { id: styleId, name: s.name, styleType: s.styleType, ...(s.remote ? { remote: true } : {}) };
      else {
        out.styles[role] = { id: styleId };
        addMissing(ctx, n, `styles.${role}`, 'unresolved', `スタイル ${styleId} の名前が応答にない`);
      }
    }
  }

  if (n.type === 'TEXT') {
    out.text = normalizeText(n);
    if (!n.style) addMissing(ctx, n, 'style', 'absent', 'テキストのスタイルが応答にない');
    const s = n.style || {};
    ctx.facts.texts.push({ id: n.id, name: n.name, characters: n.characters, path: pathNames.join(' / '), ...(hidden ? { hidden: true } : {}) });
    const key = [s.fontFamily, s.fontWeight, s.fontSize, s.lineHeightPx, s.letterSpacing].join('|');
    const prev = ctx.facts.typography.get(key) || {
      fontFamily: s.fontFamily, fontWeight: s.fontWeight, fontSize: s.fontSize, lineHeightPx: s.lineHeightPx,
      letterSpacing: s.letterSpacing, style: out.styles?.text?.name, count: 0, nodeIds: [],
    };
    prev.count += 1;
    if (prev.nodeIds.length < 20) prev.nodeIds.push(n.id);
    ctx.facts.typography.set(key, prev);
  }

  for (const [kind, list] of [['fill', n.fills], ['stroke', n.strokes]]) {
    for (const p of list || []) {
      if (p.type === 'SOLID' && p.color && p.visible !== false) {
        const hex = colorToHex(p.color);
        const alpha = round((p.color.a ?? 1) * (p.opacity ?? 1));
        const key = `${hex}|${alpha}`;
        const prev = ctx.facts.colors.get(key) || { color: hex, alpha, uses: new Set(), styles: new Set(), count: 0 };
        prev.count += 1;
        prev.uses.add(kind);
        const styleName = out.styles?.[kind]?.name || out.styles?.[`${kind}s`]?.name;
        if (styleName) prev.styles.add(styleName);
        ctx.facts.colors.set(key, prev);
      }
      if (p.imageRef) {
        const prev = ctx.facts.imageFills.get(p.imageRef) || { imageRef: p.imageRef, nodeIds: [], scaleMode: p.scaleMode };
        if (!prev.nodeIds.includes(n.id)) prev.nodeIds.push(n.id);
        ctx.facts.imageFills.set(p.imageRef, prev);
      }
    }
  }

  if (n.type === 'INSTANCE') {
    const meta = ctx.components[n.componentId];
    const set = meta?.componentSetId ? ctx.componentSets[meta.componentSetId] : null;
    out.component = {
      componentId: n.componentId,
      ...(meta ? { name: meta.name, key: meta.key, ...(meta.remote ? { remote: true } : {}) } : {}),
      ...(set ? { setName: set.name, setId: meta.componentSetId } : {}),
    };
    if (n.componentProperties) {
      out.component.properties = Object.fromEntries(
        Object.entries(n.componentProperties).map(([k, v]) => [k, { type: v.type, value: v.value, ...(v.boundVariables ? { boundVariables: v.boundVariables } : {}) }]),
      );
    }
    if (n.overrides?.length) out.component.overriddenFields = n.overrides.map((o) => ({ id: o.id, fields: o.overriddenFields }));
    if (!meta) addMissing(ctx, n, 'component', 'absent', `メインコンポーネント ${n.componentId} のメタデータが応答にない`);
    const key = set ? `set:${meta.componentSetId}` : `c:${n.componentId}`;
    const usage = ctx.facts.components.get(key) || {
      name: set?.name || meta?.name || n.name,
      componentId: n.componentId,
      componentSetId: meta?.componentSetId,
      key: set?.key || meta?.key,
      remote: Boolean(meta?.remote),
      instances: [],
      variants: new Set(),
    };
    usage.instances.push(n.id);
    if (set && meta?.name) usage.variants.add(meta.name);
    ctx.facts.components.set(key, usage);
    const mapped = lookupMapping(ctx.mapping, { componentId: n.componentId, componentSetId: meta?.componentSetId, key: meta?.key, setKey: set?.key, name: usage.name });
    if (mapped) {
      out.codeMapping = mapped;
      usage.codeMapping = mapped;
    }
  }
  if (n.type === 'COMPONENT' || n.type === 'COMPONENT_SET') {
    const meta = n.type === 'COMPONENT' ? ctx.components[n.id] : ctx.componentSets[n.id];
    out.component = {
      ...(meta ? pick(meta, ['key', 'description', 'componentSetId', 'documentationLinks']) : {}),
      ...(n.componentPropertyDefinitions ? { propertyDefinitions: n.componentPropertyDefinitions } : {}),
    };
    const mapped = lookupMapping(ctx.mapping, { componentId: n.id, key: meta?.key, name: n.name });
    if (mapped) out.codeMapping = mapped;
  }
  if (n.componentPropertyReferences) out.componentPropertyReferences = n.componentPropertyReferences;

  const bindings = collectBindings(n);
  if (bindings.length) {
    out.variables = bindings.map((b) => {
      const resolved = resolveVariable(b.variableId, ctx.variables);
      const fact = ctx.facts.bindings.get(b.variableId) || { variableId: b.variableId, ...(resolved || {}), nodeIds: [], fields: new Set() };
      if (fact.nodeIds.length < 20 && !fact.nodeIds.includes(n.id)) fact.nodeIds.push(n.id);
      fact.fields.add(b.field.replace(/\[\d+\]/g, '[]'));
      ctx.facts.bindings.set(b.variableId, fact);
      if (!resolved) {
        const why = ctx.variablesStatus === 'not_requested'
          ? '変数の値は解決していない（--variables で解決を試せる。Enterprise プランのみ）'
          : ctx.variablesStatus === 'inaccessible'
            ? '変数 API にアクセスできないためバインド ID のみ保持'
            : '変数 API の応答にこの ID がない（他ファイルのライブラリ変数の可能性）';
        addMissing(ctx, n, b.field, ctx.variablesStatus === 'inaccessible' ? 'inaccessible' : 'unresolved', `${b.variableId}: ${why}`);
      }
      return { ...b, ...(resolved ? { resolved } : {}) };
    });
  }

  if (n.annotations?.length) {
    out.annotations = n.annotations;
    ctx.facts.annotations.push({ id: n.id, name: n.name, annotations: n.annotations });
  }
  if (n.interactions?.length) {
    out.interactions = n.interactions.map((it) => ({
      trigger: it.trigger,
      actions: (it.actions || []).filter(Boolean).map((a) => pick(a, ['type', 'destinationId', 'navigation', 'transition', 'url', 'preserveScrollPosition']) || {}),
    }));
    ctx.facts.interactions.push({ id: n.id, name: n.name, path: pathNames.join(' / '), interactions: out.interactions, actions: out.interactions.flatMap((i) => i.actions) });
  }
  if (n.transitionNodeID) out.transitionNodeID = n.transitionNodeID;
  if (n.exportSettings?.length) out.exportSettings = n.exportSettings;
  if (n.devStatus) out.devStatus = n.devStatus;

  if (Array.isArray(n.children)) {
    out.children = n.children.map((c) => normalizeNode(c, ctx, level + 1, pathNames, [...ancestorIds, n.id], hidden));
  } else if (CONTAINER_TYPES.has(n.type) && ctx.depth !== null && level >= ctx.depth) {
    out.childrenStatus = 'not_fetched';
    addMissing(ctx, n, 'children', 'not_fetched', `depth=${ctx.depth} の制限で子を取得していない`);
  }

  if (isSvgCandidate(n)) ctx.facts.svgCandidates.push({ id: n.id, name: n.name, type: n.type, path: pathNames.join(' / '), ancestorIds, ...(hidden ? { hidden: true } : {}) });
  return out;
}

function dropNested(candidates) {
  const ids = new Set(candidates.map((c) => c.id));
  return candidates
    .filter((c) => !c.ancestorIds.some((a) => ids.has(a)))
    .map(({ ancestorIds, ...rest }) => rest)
    .reverse();
}

function isSvgCandidate(n) {
  if (VECTOR_TYPES.has(n.type) && n.type !== 'RECTANGLE' && n.type !== 'LINE') return true;
  if (['FRAME', 'GROUP', 'INSTANCE', 'COMPONENT'].includes(n.type) && Array.isArray(n.children) && n.children.length) {
    const allVector = (node) => (Array.isArray(node.children) && node.children.length ? node.children.every(allVector) : VECTOR_TYPES.has(node.type));
    const small = n.absoluteBoundingBox && n.absoluteBoundingBox.width <= 64 && n.absoluteBoundingBox.height <= 64;
    return small && n.children.every(allVector);
  }
  return false;
}

/** 手動マッピング・Code Connect から、コンポーネントに対応するコード側の部品を探す。 */
export function lookupMapping(mapping, { componentId, componentSetId, key, setKey, name }) {
  if (!mapping) return null;
  for (const k of [componentId, componentSetId, key, setKey, name]) {
    if (k && mapping[k]) return mapping[k];
  }
  return null;
}

/** JSON に書けるよう Set を配列にする。 */
export function toPlain(value) {
  return JSON.parse(JSON.stringify(value, (k, v) => (v instanceof Set ? [...v] : v instanceof Map ? Object.fromEntries(v) : v)));
}
