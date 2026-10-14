import type { EditorNotice } from './types';

const NS = 'http://www.w3.org/2000/svg';
const TAGS = new Set(['svg', 'g', 'defs', 'symbol', 'use', 'image', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'path', 'text', 'tspan', 'clipPath', 'mask', 'linearGradient', 'radialGradient', 'stop', 'pattern']);
const ATTRS = new Set(['id', 'viewBox', 'preserveAspectRatio', 'width', 'height', 'x', 'y', 'x1', 'x2', 'y1', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'points', 'd', 'transform', 'clip-path', 'mask', 'fill', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-dasharray', 'stroke-opacity', 'opacity', 'fill-rule', 'clip-rule', 'font-size', 'font-family', 'font-weight', 'text-anchor', 'dominant-baseline', 'href', 'offset', 'stop-color', 'stop-opacity', 'gradientUnits', 'gradientTransform', 'patternUnits', 'patternContentUnits', 'maskUnits', 'maskContentUnits', 'clipPathUnits', 'spreadMethod']);
const REF_ATTRS = new Set(['clip-path', 'mask', 'fill', 'stroke', 'href']);
const RASTER_DATA = /^data:image\/(?:png|jpeg|webp|gif);base64,[a-z0-9+/=]+$/i;
const NUMBER_LIST = /^[\d\s.,+\-eE%]*$/;
const FRAGMENT = /^#[A-Za-z_][\w:.-]*$/;
const URL_FRAGMENT = /^url\(\s*(['"]?)#([A-Za-z_][\w:.-]*)\1\s*\)$/;

export interface PreparedSvg { markup: string; imageIds: string[]; notices: EditorNotice[] }

export function sanitizeSvg(source: string, prefix: string): PreparedSvg {
  const notices: EditorNotice[] = [];
  if (/<!DOCTYPE|<!ENTITY/i.test(source)) throw new Error('DTD and entities are not supported');
  const parsed = new DOMParser().parseFromString(source, 'image/svg+xml');
  const root = parsed.documentElement;
  if (root.localName !== 'svg' || root.namespaceURI !== NS || parsed.querySelector('parsererror')) throw new Error('Invalid SVG');
  const ids = new Set<string>();
  const imageIds: string[] = [];
  const unsupported = new Set<string>();
  const output = document.implementation.createDocument(NS, 'svg', null);
  const safePrefix = prefix.replace(/[^A-Za-z0-9_-]/g, '_');
  const pending: Array<{ element: Element; attr: string; id: string; url: boolean }> = [];
  const removed = new Set<string>();

  function notice(detail: string) { removed.add(detail); }
  function visit(input: Element, parent: Element, isRoot = false): void {
    if (input.namespaceURI !== NS || !TAGS.has(input.localName)) { notice(`element <${input.localName}>`); return; }
    const element = isRoot ? parent : output.createElementNS(NS, input.localName);
    if (!isRoot) parent.appendChild(element);
    let originalId: string | null = null;
    for (const attr of Array.from(input.attributes)) {
      const name = attr.localName;
      const value = attr.value.trim();
      if (attr.namespaceURI === 'http://www.w3.org/2000/xmlns/') continue;
      if (!ATTRS.has(name) || (attr.namespaceURI && attr.namespaceURI !== 'http://www.w3.org/1999/xlink')) { notice(`attribute ${name}`); continue; }
      if (name === 'id') {
        if (!/^[A-Za-z_][\w:.-]*$/.test(value) || ids.has(value)) { notice(`duplicate or invalid id ${value}`); continue; }
        ids.add(value); originalId = value; element.setAttribute('id', `${safePrefix}-${value}`); continue;
      }
      if (name === 'href') {
        if (input.localName === 'image') {
          if (!RASTER_DATA.test(value)) { notice('external or unsafe image URL'); continue; }
          element.setAttribute('href', value);
        } else if (FRAGMENT.test(value)) pending.push({ element, attr: name, id: value.slice(1), url: false });
        else notice('unsafe href');
        continue;
      }
      if (REF_ATTRS.has(name) && /url\s*\(/i.test(value)) {
        const match = value.match(URL_FRAGMENT);
        if (match) pending.push({ element, attr: name, id: match[2], url: true });
        else notice(`unsafe ${name} URL`);
        continue;
      }
      if (/url\s*\(|expression\s*\(|[<>;]/i.test(value)) { notice(`unsafe ${name}`); continue; }
      if (['transform', 'gradientTransform'].includes(name) && !/^[\w\d\s.,()+\-eE]*$/.test(value)) { notice(`invalid ${name}`); continue; }
      if (['width', 'height', 'x', 'y', 'x1', 'x2', 'y1', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'opacity', 'stroke-width', 'font-size'].includes(name) && !NUMBER_LIST.test(value)) { notice(`invalid ${name}`); continue; }
      element.setAttribute(name, value);
    }
    if (input.localName === 'image' && originalId && element.hasAttribute('href')) {
      element.setAttribute('data-editor-id', originalId);
      imageIds.push(originalId);
      if (input.hasAttribute('clip-path') || input.hasAttribute('mask')) unsupported.add(`image ${originalId} has its own clip or mask`);
    } else if (input.localName === 'image') {
      notice('image without stable ID or safe raster data');
    }
    for (const child of Array.from(input.childNodes)) {
      if (child.nodeType === Node.ELEMENT_NODE) visit(child as Element, element);
      else if (child.nodeType === Node.TEXT_NODE && (input.localName === 'text' || input.localName === 'tspan')) element.appendChild(output.createTextNode(child.textContent ?? ''));
    }
  }
  visit(root, output.documentElement, true);
  if (!output.documentElement.hasAttribute('viewBox')) throw new Error('SVG root requires a viewBox');
  output.documentElement.setAttribute('width', '100%');
  output.documentElement.setAttribute('height', '100%');
  for (const ref of pending) {
    if (ids.has(ref.id)) ref.element.setAttribute(ref.attr, ref.url ? `url(#${safePrefix}-${ref.id})` : `#${safePrefix}-${ref.id}`);
    else notice(`unresolved reference #${ref.id}`);
  }
  if (removed.size) notices.push({ code: 'removed-content', message: `SVG content removed: ${Array.from(removed).join(', ')}` });
  if (unsupported.size) notices.push({ code: 'unsupported', message: `Unsupported editing structure: ${Array.from(unsupported).join(', ')}` });
  return { markup: new XMLSerializer().serializeToString(output.documentElement), imageIds, notices };
}
