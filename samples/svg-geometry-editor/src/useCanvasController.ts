import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent } from 'react';
import { around, clamp, decompose, DEFAULT_VIEW, fromDOM, IDENTITY, matrixString, multiply, point, rotate, scale, translate, zoomAt } from './geometry';
import type { Point } from './geometry';
import { sameEdits, validateEdits } from './edits';
import { angle, distance, midpoint, resolvePointerEnd } from './gesture';
import type { Gesture } from './gesture';
import { sanitizeSvg } from './sanitize';
import type { EditorNotice, Edits, Matrix, SvgGeometryCanvasProps, View } from './types';

type Overlay = { corners: Point[]; center: Point; rotation: Point };
const SVG_NS = 'http://www.w3.org/2000/svg';

function localOf(client: Point, host: HTMLElement): Point {
  const rect = host.getBoundingClientRect();
  return { x: client.x - rect.left, y: client.y - rect.top };
}
function mapped(matrix: DOMMatrix, p: Point): Point {
  return point(fromDOM(matrix), p);
}
function selectedImage(target: EventTarget | null): string | null {
  if (!(target instanceof Element)) return null;
  return target.closest('[data-editor-id]')?.getAttribute('data-editor-id') ?? null;
}

export function useCanvasController(props: SvgGeometryCanvasProps) {
  const { svg, mode, value, onChange, onSelectionChange, onViewChange } = props;
  const minScale = Math.max(0.01, props.minScale ?? 0.1);
  const maxScale = Math.max(minScale, props.maxScale ?? 10);
  const moveStep = props.moveStep ?? 1;
  const onErrorRef = useRef(props.onError);
  onErrorRef.current = props.onError;
  const prefix = useId();
  const prepared = useMemo(() => {
    try { return { svg: sanitizeSvg(svg, prefix), error: null as string | null }; }
    catch (error) { return { svg: null, error: (error as Error).message }; }
  }, [svg, prefix]);
  const ids = useMemo(() => new Set(prepared.svg?.imageIds ?? []), [prepared.svg]);
  const { edits, dropped } = useMemo(() => validateEdits(value, ids), [value, ids]);
  // The latest edits, including ones emitted but not yet rendered by the caller.
  const latest = useRef(edits);
  latest.current = edits;
  const selection = props.selectedId && ids.has(props.selectedId) ? props.selectedId : null;
  const hostRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<HTMLDivElement>(null);
  const images = useRef(new Map<string, SVGImageElement>());
  const wrappers = useRef(new Map<string, SVGGElement>());
  const pointers = useRef(new Map<number, Point>());
  const gesture = useRef<Gesture>({ kind: 'idle' });
  const [view, setView] = useState<View>(DEFAULT_VIEW);
  const viewRef = useRef(view);
  const [overlay, setOverlay] = useState<Overlay | null>(null);
  const [geometryTick, setGeometryTick] = useState(0);

  const notify = useCallback((code: EditorNotice['code'], message: string) => onErrorRef.current?.({ code, message }), []);
  useEffect(() => {
    if (prepared.error) notify('invalid-svg', prepared.error);
    prepared.svg?.notices.forEach(n => notify(n.code, n.message));
  }, [prepared, notify]);
  useEffect(() => {
    if (dropped.length) notify('invalid-data', `Edits that do not match an editable image were ignored: ${dropped.join(', ')}`);
  }, [dropped, notify]);

  const refreshGeometry = useCallback(() => setGeometryTick(t => t + 1), []);
  useLayoutEffect(() => {
    images.current.clear(); wrappers.current.clear();
    const root = sceneRef.current?.querySelector('svg');
    if (!root) return;
    for (const image of Array.from(root.querySelectorAll<SVGImageElement>('image[data-editor-id]'))) {
      const id = image.getAttribute('data-editor-id')!;
      const wrapper = document.createElementNS(SVG_NS, 'g');
      wrapper.setAttribute('data-editor-wrapper', id);
      image.parentNode?.insertBefore(wrapper, image);
      wrapper.appendChild(image);
      images.current.set(id, image); wrappers.current.set(id, wrapper);
    }
    refreshGeometry();
  }, [prepared.svg?.markup, refreshGeometry]);
  useLayoutEffect(() => {
    for (const [id, wrapper] of wrappers.current) wrapper.setAttribute('transform', matrixString(edits[id] ?? IDENTITY));
    refreshGeometry();
  }, [edits, prepared.svg?.markup, refreshGeometry]);
  useLayoutEffect(() => {
    const image = selection ? images.current.get(selection) : null;
    const host = hostRef.current;
    if (!image || !host) { setOverlay(null); return; }
    try {
      const box = image.getBBox();
      const ctm = image.getScreenCTM();
      if (!ctm) { setOverlay(null); return; }
      const rect = host.getBoundingClientRect();
      const convert = (x: number, y: number): Point => {
        const p = mapped(ctm, { x, y }); return { x: p.x - rect.left, y: p.y - rect.top };
      };
      const corners = [convert(box.x, box.y), convert(box.x + box.width, box.y), convert(box.x + box.width, box.y + box.height), convert(box.x, box.y + box.height)];
      const center = convert(box.x + box.width / 2, box.y + box.height / 2);
      const top = midpoint(corners[0], corners[1]);
      const vx = top.x - center.x, vy = top.y - center.y, length = Math.hypot(vx, vy) || 1;
      setOverlay({ corners, center, rotation: { x: top.x + vx / length * 38, y: top.y + vy / length * 38 } });
    } catch { setOverlay(null); }
  }, [selection, geometryTick, view, edits]);
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const observer = new ResizeObserver(refreshGeometry);
    observer.observe(host);
    const update = () => refreshGeometry();
    window.addEventListener('resize', update);
    window.visualViewport?.addEventListener('resize', update);
    return () => { observer.disconnect(); window.removeEventListener('resize', update); window.visualViewport?.removeEventListener('resize', update); };
  }, [refreshGeometry]);
  useEffect(() => { refreshGeometry(); }, [view, refreshGeometry]);

  const matrixFor = (id: string): Matrix => latest.current[id] ?? IDENTITY;
  function setSelection(id: string | null) { if (id !== selection) onSelectionChange(id); }
  function emit(next: Edits, final: boolean) { latest.current = next; onChange(next, { final }); }
  function applyEdit(id: string, matrix: Matrix) { emit({ ...latest.current, [id]: matrix }, false); }
  function finish(before: Edits) { if (!sameEdits(before, latest.current)) emit(latest.current, true); }
  function cancelGesture() {
    const g = gesture.current;
    if (g.kind === 'idle') return;
    if ('before' in g) emit(g.before, false);
    gesture.current = { kind: 'blocked', pointers: new Set(pointers.current.keys()) };
  }
  function changeView(next: View) { viewRef.current = next; setView(next); onViewChange?.(next); }
  function zoom(factor: number) {
    const host = hostRef.current;
    if (host) changeView(zoomAt(viewRef.current, { x: host.clientWidth / 2, y: host.clientHeight / 2 }, factor));
  }
  function resetView() { changeView(DEFAULT_VIEW); }
  function parentInverseOf(id: string) {
    const parent = wrappers.current.get(id)?.parentElement as SVGGraphicsElement | null | undefined;
    return parent?.getScreenCTM()?.inverse();
  }

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    const host = hostRef.current;
    if (!host || event.button !== 0 && event.pointerType === 'mouse') return;
    host.focus({ preventScroll: true });
    host.setPointerCapture(event.pointerId);
    const client = { x: event.clientX, y: event.clientY };
    pointers.current.set(event.pointerId, client);
    const current = gesture.current;
    if (current.kind === 'blocked') { current.pointers.add(event.pointerId); return; }
    if (current.kind === 'image-drag' && selectedImage(event.target) === current.id && pointers.current.size === 2) {
      const other = pointers.current.get(current.pointer)!;
      const parentInverse = parentInverseOf(current.id);
      if (parentInverse) gesture.current = { kind: 'image-pinch', id: current.id, pointers: [current.pointer, event.pointerId], start: [other, client], startMatrix: matrixFor(current.id), before: current.before, parentInverse };
      return;
    }
    if (current.kind === 'view-pan' && pointers.current.size === 2) {
      gesture.current = { kind: 'view-pinch', pointers: [current.pointer, event.pointerId], start: [pointers.current.get(current.pointer)!, client], startView: viewRef.current };
      return;
    }
    if (current.kind !== 'idle') {
      if ('before' in current) finish(current.before);
      gesture.current = { kind: 'blocked', pointers: new Set(pointers.current.keys()) };
      return;
    }
    if (mode === 'edit') {
      const handle = (event.target as Element).closest('[data-handle]')?.getAttribute('data-handle');
      if (selection && handle && overlay) {
        const parentInverse = parentInverseOf(selection);
        if (!parentInverse) return;
        const before = latest.current, startMatrix = matrixFor(selection);
        const localPivot = handle === 'rotate' ? overlay.center : overlay.corners[(Number(handle) + 2) % 4];
        const rect = host.getBoundingClientRect();
        const pivotScreen = { x: localPivot.x + rect.left, y: localPivot.y + rect.top };
        const pivot = mapped(parentInverse, pivotScreen);
        if (handle === 'rotate') gesture.current = { kind: 'image-rotate', id: selection, pointer: event.pointerId, start: client, startMatrix, before, pivot, pivotScreen };
        else gesture.current = { kind: 'image-resize', id: selection, pointer: event.pointerId, start: client, startMatrix, before, pivot, pivotScreen, parentInverse };
        return;
      }
      const imageId = selectedImage(event.target);
      if (imageId && ids.has(imageId)) {
        setSelection(imageId);
        const parentInverse = parentInverseOf(imageId);
        if (parentInverse) gesture.current = { kind: 'image-drag', id: imageId, pointer: event.pointerId, start: client, startMatrix: matrixFor(imageId), before: latest.current, parentInverse };
        return;
      }
      setSelection(null);
    }
    gesture.current = { kind: 'view-pan', pointer: event.pointerId, start: client, startView: viewRef.current };
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!pointers.current.has(event.pointerId)) return;
    const client = { x: event.clientX, y: event.clientY };
    pointers.current.set(event.pointerId, client);
    const g = gesture.current;
    const scaleLimits = (m: Matrix) => [minScale / decompose(m).scale, maxScale / decompose(m).scale] as const;
    if (g.kind === 'image-drag' && event.pointerId === g.pointer) {
      const a = mapped(g.parentInverse, g.start), b = mapped(g.parentInverse, client);
      applyEdit(g.id, multiply(translate(b.x - a.x, b.y - a.y), g.startMatrix));
    } else if (g.kind === 'image-resize' && event.pointerId === g.pointer) {
      const original = distance(g.start, g.pivotScreen);
      if (original < 1) return;
      const factor = clamp(distance(client, g.pivotScreen) / original, ...scaleLimits(g.startMatrix));
      applyEdit(g.id, multiply(around(g.pivot, scale(factor)), g.startMatrix));
    } else if (g.kind === 'image-rotate' && event.pointerId === g.pointer) {
      const delta = angle(g.pivotScreen, client) - angle(g.pivotScreen, g.start);
      applyEdit(g.id, multiply(around(g.pivot, rotate(delta)), g.startMatrix));
    } else if (g.kind === 'image-pinch') {
      const [a, b] = g.pointers.map(id => pointers.current.get(id)) as [Point | undefined, Point | undefined];
      if (!a || !b) return;
      const startCenter = midpoint(...g.start), center = midpoint(a, b);
      const startLocal = mapped(g.parentInverse, startCenter), endLocal = mapped(g.parentInverse, center);
      const factor = clamp(distance(a, b) / Math.max(1, distance(...g.start)), ...scaleLimits(g.startMatrix));
      const transform = multiply(translate(endLocal.x - startLocal.x, endLocal.y - startLocal.y), around(startLocal, scale(factor)));
      applyEdit(g.id, multiply(transform, g.startMatrix));
    } else if (g.kind === 'view-pan' && event.pointerId === g.pointer) {
      changeView({ ...g.startView, x: g.startView.x + client.x - g.start.x, y: g.startView.y + client.y - g.start.y });
    } else if (g.kind === 'view-pinch') {
      const [a, b] = g.pointers.map(id => pointers.current.get(id)) as [Point | undefined, Point | undefined];
      const host = hostRef.current;
      if (!a || !b || !host) return;
      const start = localOf(midpoint(...g.start), host), center = localOf(midpoint(a, b), host);
      const next = zoomAt(g.startView, start, distance(a, b) / Math.max(1, distance(...g.start)));
      changeView({ ...next, x: next.x + center.x - start.x, y: next.y + center.y - start.y });
    }
  }
  function finishPointer(event: PointerEvent<HTMLDivElement>, canceled: boolean) {
    const g = gesture.current;
    const resolution = resolvePointerEnd(g, event.pointerId, pointers.current, canceled);
    if (resolution.action === 'restore' && 'before' in g) emit(g.before, false);
    if (resolution.action === 'commit' && 'before' in g) finish(g.before);
    pointers.current.delete(event.pointerId);
    gesture.current = resolution.next;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') { cancelGesture(); event.preventDefault(); return; }
    if (mode !== 'edit' || !selection || gesture.current.kind !== 'idle') return;
    const step = moveStep * (event.shiftKey ? 10 : 1);
    const moves: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const move = moves[event.key];
    if (!move) return;
    emit({ ...latest.current, [selection]: multiply(translate(...move), matrixFor(selection)) }, true);
    event.preventDefault();
  }
  // React registers wheel listeners as passive, so page scrolling could not be stopped from onWheel.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      changeView(zoomAt(viewRef.current, localOf({ x: event.clientX, y: event.clientY }, host), Math.exp(-event.deltaY * 0.002)));
    };
    host.addEventListener('wheel', onWheel, { passive: false });
    return () => host.removeEventListener('wheel', onWheel);
  });
  useEffect(() => {
    const cancel = () => { if (gesture.current.kind !== 'idle') cancelGesture(); pointers.current.clear(); gesture.current = { kind: 'idle' }; };
    window.addEventListener('blur', cancel);
    document.addEventListener('visibilitychange', cancel);
    return () => { window.removeEventListener('blur', cancel); document.removeEventListener('visibilitychange', cancel); };
  });
  useEffect(() => {
    if (gesture.current.kind !== 'idle') cancelGesture();
    pointers.current.clear(); gesture.current = { kind: 'idle' };
  }, [mode]);

  return { prepared, selection, overlay, view, hostRef, sceneRef, zoom, resetView,
    onPointerDown, onPointerMove, onPointerUp: (e: PointerEvent<HTMLDivElement>) => finishPointer(e, false),
    onPointerCancel: (e: PointerEvent<HTMLDivElement>) => finishPointer(e, true), onKeyDown };
}
