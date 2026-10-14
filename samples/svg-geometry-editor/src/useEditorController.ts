import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent, WheelEvent } from 'react';
import { around, clamp, decompose, DEFAULT_VIEW, fromDOM, matrixString, multiply, point, rotate, scale, translate, zoomAt } from './geometry';
import type { Point, View } from './geometry';
import { emptyDocument, History, matrixFor, validateDocument, withMatrix, withoutMatrix } from './document';
import { angle, distance, midpoint, resolvePointerEnd } from './gesture';
import type { Gesture } from './gesture';
import { sanitizeSvg } from './sanitize';
import type { EditTool, EditorDocument, Matrix, SvgGeometryEditorProps } from './types';

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

export function useEditorController(props: SvgGeometryEditorProps) {
  const { svg, templateId, templateRevision, mode, onChange, onCommit, onError, onSelectionChange } = props;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const prefix = useId();
  const prepared = useMemo(() => {
    try { return { svg: sanitizeSvg(svg, prefix), error: null as string | null }; }
    catch (error) { return { svg: null, error: (error as Error).message }; }
  }, [svg, prefix]);
  const ids = useMemo(() => new Set(prepared.svg?.imageIds ?? []), [prepared.svg]);
  const [doc, setDoc] = useState<EditorDocument>(() => props.value ?? props.defaultValue ?? emptyDocument(templateId, templateRevision));
  const docRef = useRef(doc);
  const history = useRef(new History(doc));
  const hostRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<HTMLDivElement>(null);
  const images = useRef(new Map<string, SVGImageElement>());
  const wrappers = useRef(new Map<string, SVGGElement>());
  const pointers = useRef(new Map<number, Point>());
  const gesture = useRef<Gesture>({ kind: 'idle' });
  const numberBefore = useRef<EditorDocument | null>(null);
  const [view, setView] = useState<View>(DEFAULT_VIEW);
  const viewRef = useRef(view);
  const [tool, setTool] = useState<EditTool>('select');
  const [internalSelection, setInternalSelection] = useState<string | null>(null);
  const selection = props.selectedId === undefined ? internalSelection : props.selectedId;
  const [overlay, setOverlay] = useState<Overlay | null>(null);
  const [geometryTick, setGeometryTick] = useState(0);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const minScale = Math.max(0.01, props.constraints?.minScale ?? 0.1);
  const constraints = { minScale, maxScale: Math.max(minScale, props.constraints?.maxScale ?? 10), moveStep: props.constraints?.moveStep ?? 1, rotationStep: props.constraints?.rotationStep ?? 1 };
  const valid = useMemo(() => {
    try { return validateDocument(doc, templateId, templateRevision, ids); }
    catch { return emptyDocument(templateId, templateRevision); }
  }, [doc, templateId, templateRevision, ids]);

  const notify = useCallback((code: 'invalid-svg' | 'removed-content' | 'unsupported' | 'invalid-data', message: string) => onErrorRef.current?.({ code, message }), []);
  useEffect(() => {
    if (prepared.error) notify('invalid-svg', prepared.error);
    prepared.svg?.notices.forEach(n => notify(n.code, n.message));
  }, [prepared, notify]);
  useEffect(() => {
    if (doc !== valid) {
      notify('invalid-data', 'Edit JSON did not match the SVG template; edits were ignored.');
      docRef.current = valid; history.current.replace(valid); setDoc(valid);
    }
  }, [doc, valid, notify]);
  useEffect(() => {
    if (!props.value || props.value === docRef.current) return;
    try {
      const external = validateDocument(props.value, templateId, templateRevision, ids);
      if (JSON.stringify(external) !== JSON.stringify(docRef.current)) {
        docRef.current = external; history.current.replace(external); setDoc(external);
      }
    } catch (error) { notify('invalid-data', (error as Error).message); }
  }, [props.value, ids, templateId, templateRevision, notify]);
  useEffect(() => {
    const fresh = emptyDocument(templateId, templateRevision);
    if (docRef.current.templateId !== templateId || docRef.current.templateRevision !== templateRevision) {
      docRef.current = fresh; history.current.replace(fresh); setDoc(fresh); setInternalSelection(null);
    }
  }, [templateId, templateRevision]);

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
    for (const [id, wrapper] of wrappers.current) wrapper.setAttribute('transform', matrixString(matrixFor(valid, id)));
    refreshGeometry();
  }, [valid, refreshGeometry]);
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
  }, [selection, geometryTick, view, valid]);
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

  function setSelection(id: string | null) { finishNumber(); setInternalSelection(id); onSelectionChange?.(id); }
  function preview(value: EditorDocument) {
    docRef.current = value; history.current.preview(value); setDoc(value); onChange?.(value);
  }
  function commit(before: EditorDocument, after: EditorDocument) {
    if (history.current.commit(before, after)) onCommit?.(after);
  }
  function applyEdit(id: string, matrix: Matrix, before = docRef.current, done = true) {
    const next = withMatrix(docRef.current, id, matrix);
    preview(next);
    if (done) commit(before, next);
  }
  function replaceDocument(value: EditorDocument) {
    docRef.current = value; setDoc(value); onChange?.(value); onCommit?.(value);
  }
  function cancelGesture() {
    const g = gesture.current;
    if (g.kind === 'idle') return;
    if ('before' in g) preview(g.before);
    gesture.current = { kind: 'blocked', pointers: new Set(pointers.current.keys()) };
  }
  function resetView() { setView(DEFAULT_VIEW); viewRef.current = DEFAULT_VIEW; }
  function changeView(next: View) { viewRef.current = next; setView(next); }
  function zoom(factor: number) {
    const host = hostRef.current;
    if (!host) return;
    changeView(zoomAt(viewRef.current, { x: host.clientWidth / 2, y: host.clientHeight / 2 }, factor));
  }
  function undo() { const value = history.current.undo(); if (value) replaceDocument(value); }
  function redo() { const value = history.current.redo(); if (value) replaceDocument(value); }
  function resetImage() {
    if (!selection) return;
    const before = docRef.current; const next = withoutMatrix(before, selection);
    preview(next); commit(before, next);
  }
  function finishNumber() {
    if (numberBefore.current) commit(numberBefore.current, docRef.current);
    numberBefore.current = null;
  }
  function editNumber(field: 'x' | 'y' | 'scale' | 'angle', value: number) {
    if (!selection || !Number.isFinite(value)) return;
    if (!numberBefore.current) numberBefore.current = docRef.current;
    const before = docRef.current;
    const matrix = matrixFor(before, selection);
    const parts = decompose(matrix);
    let next: Matrix;
    if (field === 'x') next = [...matrix.slice(0, 4), value, matrix[5]] as Matrix;
    else if (field === 'y') next = [...matrix.slice(0, 5), value] as Matrix;
    else if (field === 'scale') next = multiply(around({ x: matrix[4], y: matrix[5] }, scale(clamp(value, constraints.minScale, constraints.maxScale) / parts.scale)), matrix);
    else next = multiply(around({ x: matrix[4], y: matrix[5] }, rotate((value - parts.angle) * Math.PI / 180)), matrix);
    applyEdit(selection, next, before, false);
  }
  function nudge(dx: number, dy: number) {
    if (!selection) return;
    const before = docRef.current;
    applyEdit(selection, multiply(translate(dx, dy), matrixFor(before, selection)), before);
  }
  function rotateBy(degrees: number) {
    if (!selection) return;
    const before = docRef.current, matrix = matrixFor(before, selection);
    applyEdit(selection, multiply(around({ x: matrix[4], y: matrix[5] }, rotate(degrees * Math.PI / 180)), matrix), before);
  }
  function scaleBy(amount: number) {
    if (!selection) return;
    const before = docRef.current, matrix = matrixFor(before, selection);
    const current = decompose(matrix).scale;
    const next = clamp(current + amount, constraints.minScale, constraints.maxScale);
    applyEdit(selection, multiply(around({ x: matrix[4], y: matrix[5] }, scale(next / current)), matrix), before);
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
    if (current.kind === 'image-drag' && mode === 'edit' && selectedImage(event.target) === current.id && pointers.current.size === 2) {
      const other = pointers.current.get(current.pointer)!;
      const parent = wrappers.current.get(current.id)?.parentElement as unknown as SVGGraphicsElement | undefined;
      const parentInverse = parent?.getScreenCTM()?.inverse();
      if (parentInverse) gesture.current = { kind: 'image-pinch', id: current.id, pointers: [current.pointer, event.pointerId], start: [other, client], startMatrix: matrixFor(docRef.current, current.id), before: current.before, parentInverse };
      return;
    }
    if (current.kind === 'view-pan' && pointers.current.size === 2) {
      gesture.current = { kind: 'view-pinch', pointers: [current.pointer, event.pointerId], start: [pointers.current.get(current.pointer)!, client], startView: viewRef.current };
      return;
    }
    if (current.kind !== 'idle') {
      if ('before' in current) commit(current.before, docRef.current);
      gesture.current = { kind: 'blocked', pointers: new Set(pointers.current.keys()) };
      return;
    }
    const handle = (event.target as Element).closest('[data-handle]')?.getAttribute('data-handle');
    const imageId = selectedImage(event.target);
    if (mode === 'edit' && tool === 'select' && selection && handle && overlay) {
      const image = images.current.get(selection), wrapper = wrappers.current.get(selection);
      const parent = wrapper?.parentElement as SVGGraphicsElement | null;
      const parentInverse = parent?.getScreenCTM()?.inverse();
      const imageCtm = image?.getScreenCTM();
      if (!image || !parentInverse || !imageCtm) return;
      const before = docRef.current, startMatrix = matrixFor(before, selection);
      const localPivot = handle === 'rotate' ? overlay.center : overlay.corners[(Number(handle) + 2) % 4];
      const pivotScreen = { x: localPivot.x + host.getBoundingClientRect().left, y: localPivot.y + host.getBoundingClientRect().top };
      const pivot = mapped(parentInverse, pivotScreen);
      if (handle === 'rotate') gesture.current = { kind: 'image-rotate', id: selection, pointer: event.pointerId, start: client, startMatrix, before, pivot, pivotScreen };
      else gesture.current = { kind: 'image-resize', id: selection, pointer: event.pointerId, start: client, startMatrix, before, pivot, pivotScreen, parentInverse };
      return;
    }
    if (mode === 'edit' && tool === 'select') {
      if (!imageId || !ids.has(imageId)) { setSelection(null); return; }
      setSelection(imageId);
      const wrapper = wrappers.current.get(imageId);
      const parent = wrapper?.parentElement as SVGGraphicsElement | null;
      const parentInverse = parent?.getScreenCTM()?.inverse();
      if (!parentInverse) return;
      gesture.current = { kind: 'image-drag', id: imageId, pointer: event.pointerId, start: client, startMatrix: matrixFor(docRef.current, imageId), before: docRef.current, parentInverse };
      return;
    }
    gesture.current = { kind: 'view-pan', pointer: event.pointerId, start: client, startView: viewRef.current };
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!pointers.current.has(event.pointerId)) return;
    const client = { x: event.clientX, y: event.clientY };
    pointers.current.set(event.pointerId, client);
    const g = gesture.current;
    if (g.kind === 'image-drag' && event.pointerId === g.pointer) {
      const a = mapped(g.parentInverse, g.start), b = mapped(g.parentInverse, client);
      applyEdit(g.id, multiply(translate(b.x - a.x, b.y - a.y), g.startMatrix), g.before, false);
    } else if (g.kind === 'image-resize' && event.pointerId === g.pointer) {
      const original = distance(g.start, g.pivotScreen);
      if (original < 1) return;
      const factor = clamp(distance(client, g.pivotScreen) / original, constraints.minScale / decompose(g.startMatrix).scale, constraints.maxScale / decompose(g.startMatrix).scale);
      applyEdit(g.id, multiply(around(g.pivot, scale(factor)), g.startMatrix), g.before, false);
    } else if (g.kind === 'image-rotate' && event.pointerId === g.pointer) {
      const delta = angle(g.pivotScreen, client) - angle(g.pivotScreen, g.start);
      applyEdit(g.id, multiply(around(g.pivot, rotate(delta)), g.startMatrix), g.before, false);
    } else if (g.kind === 'image-pinch') {
      const [a, b] = g.pointers.map(id => pointers.current.get(id)) as [Point | undefined, Point | undefined];
      if (!a || !b) return;
      const startCenter = midpoint(...g.start), center = midpoint(a, b);
      const startLocal = mapped(g.parentInverse, startCenter), endLocal = mapped(g.parentInverse, center);
      const factor = clamp(distance(a, b) / Math.max(1, distance(...g.start)), constraints.minScale / decompose(g.startMatrix).scale, constraints.maxScale / decompose(g.startMatrix).scale);
      const transform = multiply(translate(endLocal.x - startLocal.x, endLocal.y - startLocal.y), around(startLocal, scale(factor)));
      applyEdit(g.id, multiply(transform, g.startMatrix), g.before, false);
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
    if (resolution.action === 'restore' && 'before' in g) preview(g.before);
    if (resolution.action === 'commit' && 'before' in g) commit(g.before, docRef.current);
    pointers.current.delete(event.pointerId);
    gesture.current = resolution.next;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }
  function onWheel(event: WheelEvent<HTMLDivElement>) {
    if (mode !== 'view' && tool !== 'pan') return;
    event.preventDefault();
    const host = hostRef.current;
    if (!host) return;
    changeView(zoomAt(viewRef.current, localOf({ x: event.clientX, y: event.clientY }, host), Math.exp(-event.deltaY * 0.002)));
  }
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') { cancelGesture(); event.preventDefault(); return; }
    if (event.target instanceof HTMLInputElement) return;
    if (mode !== 'edit' || !selection) return;
    const step = constraints.moveStep * (event.shiftKey ? 10 : 1);
    const moves: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (moves[event.key]) { nudge(...moves[event.key]); event.preventDefault(); }
  }
  useEffect(() => {
    const cancel = () => { if (gesture.current.kind !== 'idle') cancelGesture(); pointers.current.clear(); gesture.current = { kind: 'idle' }; };
    window.addEventListener('blur', cancel);
    document.addEventListener('visibilitychange', cancel);
    return () => { window.removeEventListener('blur', cancel); document.removeEventListener('visibilitychange', cancel); };
  });
  useEffect(() => {
    if (gesture.current.kind !== 'idle') cancelGesture();
    pointers.current.clear(); gesture.current = { kind: 'idle' };
  }, [mode, tool]);

  return { prepared, valid, selection, overlay, view, tool, detailsOpen, hostRef, sceneRef, constraints,
    setTool, setDetailsOpen, setSelection, resetView, zoom, undo, redo, resetImage, editNumber, finishNumber, nudge, rotateBy, scaleBy,
    canUndo: history.current.canUndo, canRedo: history.current.canRedo,
    onPointerDown, onPointerMove, onPointerUp: (e: PointerEvent<HTMLDivElement>) => finishPointer(e, false),
    onPointerCancel: (e: PointerEvent<HTMLDivElement>) => finishPointer(e, true), onWheel, onKeyDown };
}

export type EditorController = ReturnType<typeof useEditorController>;
