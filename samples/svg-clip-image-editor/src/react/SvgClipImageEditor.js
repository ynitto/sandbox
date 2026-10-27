// SVG のクリップされた画像を、枠を固定したまま動かして合わせ込むコンポーネント。
// JSX を使わず React.createElement で書く（ビルドなしで読み込めるように）。

import React, { forwardRef, useEffect, useId, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  apply,
  bboxOf,
  drawnImageRect,
  focusRect,
  formatMatrix,
  invert,
  isCovered,
  parseClipDocument,
  resolveMotion,
  scaleOf,
  serialize,
  shapeToPath,
} from '../core/index.js';
import { EditorEngine } from './engine.js';

const h = React.createElement;

export const DEFAULT_LABELS = Object.freeze({
  region: '画像の位置を調整',
  zoomOut: '縮小',
  zoomIn: '拡大',
  rotateLeft: '左に 90°',
  rotateRight: '右に 90°',
  fit: '枠に合わせる',
  undo: '元に戻す',
  redo: 'やり直す',
  reset: 'リセット',
});

// サーバー描画ではブラウザに入るまで何もしない
const useIsoLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

const fmt = (m) => formatMatrix(m, 6);

export const SvgClipImageEditor = forwardRef(function SvgClipImageEditor(props, ref) {
  const {
    svg,
    slot: slotProp,
    focusPadding = 0.25,
    outsideOpacity = 0.35,
    toolbar = true,
    cover = true,
    rotationCover = 'rubber',
    motion: motionProp,
    minScale = 0.1,
    maxScale = 20,
    rotationSnap = 0,
    transforms,
    className,
    style,
  } = props;
  const labels = { ...DEFAULT_LABELS, ...props.labels };
  const uid = 'sce' + useId().replace(/[^A-Za-z0-9_-]/g, '');
  const svgRef = useRef(null);
  const propsRef = useRef(props);
  propsRef.current = props;

  const [parsed, setParsed] = useState(null);
  const [view, setView] = useState(null);
  const [natural, setNatural] = useState(null);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [focus, setFocus] = useState(null);
  const [, setTick] = useState(0); // 履歴のボタンの有効・無効を描き直す
  // フォーカスの枠はキーボードで入ったときだけ出す（マウス・タッチで触ったときは出さない）
  const [focusRing, setFocusRing] = useState(false);
  const pointerFocus = useRef(false);

  // ① 解析（ブラウザに入ってから）
  useEffect(() => {
    setParsed(parseClipDocument(svg));
  }, [svg]);

  const model = parsed?.ok ? parsed.model : null;
  const slot = model ? model.slots.find((s) => s.name === (slotProp ?? model.slots[0].name)) ?? null : null;
  const error = parsed && !parsed.ok ? parsed : model && !slot ? { code: 'no_slot', message: `スロット "${slotProp}" がない` } : null;

  useEffect(() => {
    if (error) propsRef.current.onError?.({ code: error.code, message: error.message, issues: error.issues });
  }, [error?.code, error?.message]);

  useEffect(() => {
    if (!model) return;
    propsRef.current.onLoad?.({
      slots: model.slots.map((s) => ({ name: s.name, bboxR: bboxOf(s.clip.hullC.map((p) => apply(s.K, p))) })),
      warnings: model.warnings,
    });
  }, [model]);

  // OS の「視差効果を減らす」
  useEffect(() => {
    const mq = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
    if (!mq) return undefined;
    const on = () => setReducedMotion(mq.matches);
    on();
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);

  const motionKey = JSON.stringify(motionProp ?? {});
  const { motion, warnings: motionWarnings } = useMemo(
    () => resolveMotion(motionProp, { reducedMotion }),
    [motionKey, reducedMotion],
  );
  useEffect(() => {
    for (const w of motionWarnings) console.warn(`SvgClipImageEditor: ${w}`);
  }, [motionWarnings]);

  // meet の画像は元の大きさが分からないと描画矩形が決まらない
  const href = slot?.image.href;
  const needsNatural = slot ? !/^\s*none\b/.test(slot.image.preserveAspectRatio) && !/slice/.test(slot.image.preserveAspectRatio) : false;
  useEffect(() => {
    if (!href || !needsNatural || typeof Image === 'undefined') return undefined;
    let alive = true;
    const img = new Image();
    img.onload = () => alive && setNatural({ href, width: img.naturalWidth, height: img.naturalHeight });
    img.src = href;
    return () => {
      alive = false;
    };
  }, [href, needsNatural]);
  const ready = Boolean(model && slot && view && focus);
  const D = slot ? drawnImageRect(slot.image, natural?.href === href ? natural : null) : null;

  // ② エンジン（動きと履歴）
  const engineRef = useRef(null);
  if (!engineRef.current) {
    engineRef.current = new EditorEngine({
      render: (M, { silent }) => {
        setView(M);
        if (!silent) propsRef.current.onChange?.(M, engineRef.current.slotName);
      },
      commit: (name, M, all) => {
        setTick((n) => n + 1);
        const p = propsRef.current;
        if (!p.onCommit) return;
        const out = serialize(engineRef.current.ctx.model, all, { precision: p.precision, outputTransform: p.outputTransform });
        p.onCommit(out, { slot: name, M });
      },
      schedule: (fn) => requestAnimationFrame(fn),
      cancel: (id) => cancelAnimationFrame(id),
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: (id) => clearTimeout(id),
      now: () => performance.now(),
    });
  }
  const engine = engineRef.current;

  const targetFocus = useMemo(() => (slot ? focusRect(slot, focusPadding) : null), [slot, focusPadding]);

  useIsoLayoutEffect(() => {
    if (!model || !slot) return;
    engine.update(
      {
        model,
        slot,
        D,
        motion,
        cover,
        rotationCover,
        minScale,
        maxScale,
        rotationSnap,
        focusWidthC: targetFocus.width / scaleOf(slot.K),
        pxPerC: () => {
          const ctm = svgRef.current?.getScreenCTM?.();
          if (!ctm) return 1;
          return Math.sqrt(Math.abs(ctm.a * ctm.d - ctm.b * ctm.c)) * scaleOf(slot.K) || 1;
        },
      },
      (name) => {
        const p = propsRef.current;
        return p.transforms?.[name] ?? p.defaultTransforms?.[name] ?? null;
      },
    );
  });

  // 制御モード: 親の値を反映する
  useEffect(() => {
    if (!slot || !transforms?.[slot.name]) return;
    engine.external(slot.name, transforms[slot.name]);
  }, [transforms, slot]);

  // 注目領域: 外からスロットを切り替えられたときだけ動く
  useEffect(() => {
    if (!targetFocus) return undefined;
    const from = focus;
    const dur = motion.snapDuration * 1000;
    if (!from || !(dur > 0)) {
      setFocus(targetFocus);
      return undefined;
    }
    let id;
    const t0 = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - t0) / dur);
      const e = 1 - (1 - t) ** 3;
      setFocus({
        x: from.x + (targetFocus.x - from.x) * e,
        y: from.y + (targetFocus.y - from.y) * e,
        width: from.width + (targetFocus.width - from.width) * e,
        height: from.height + (targetFocus.height - from.height) * e,
      });
      if (t < 1) id = requestAnimationFrame(step);
    };
    id = requestAnimationFrame(step);
    return () => cancelAnimationFrame(id);
  }, [targetFocus]);

  useEffect(() => () => engine.dispose(), []);

  // スクリーンの点 → C 空間
  const toC = (clientX, clientY) => {
    const ctm = svgRef.current.getScreenCTM();
    const inv = invert([ctm.a, ctm.b, ctm.c, ctm.d, ctm.e, ctm.f]);
    return apply(invert(engine.ctx.slot.K), apply(inv, [clientX, clientY]));
  };

  // ホイールは passive でないリスナーでないと止められない。Safari のジェスチャも止める
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return undefined;
    const onWheel = (e) => {
      e.preventDefault();
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
      const dy = (e.shiftKey ? e.deltaY || e.deltaX : e.deltaY) * unit;
      engine.wheel(toC(e.clientX, e.clientY), dy, e.shiftKey);
    };
    const stop = (e) => e.preventDefault();
    el.addEventListener('wheel', onWheel, { passive: false });
    el.addEventListener('gesturestart', stop);
    el.addEventListener('gesturechange', stop);
    return () => {
      el.removeEventListener('wheel', onWheel);
      el.removeEventListener('gesturestart', stop);
      el.removeEventListener('gesturechange', stop);
    };
  }, [ready, slot]);

  useImperativeHandle(
    ref,
    () => ({
      getSvg: () => {
        const p = propsRef.current;
        return engine.ctx ? serialize(engine.ctx.model, engine.allTransforms(), { precision: p.precision, outputTransform: p.outputTransform }) : p.svg;
      },
      getTransforms: () => engine.allTransforms(),
      reset: () => engine.reset(),
      fit: () => engine.fit(),
      zoom: (f) => engine.zoom(f),
      rotate: (deg) => engine.rotateBy(deg),
      undo: () => engine.undo(),
      redo: () => engine.redo(),
    }),
    [],
  );

  const rootStyle = { display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0, ...style };
  const rootClass = ['sce-root', className].filter(Boolean).join(' ');

  if (error) {
    return h('div', { className: rootClass, style: rootStyle, role: 'alert' }, h('p', { style: { margin: 0, color: 'var(--clip-editor-warn, #cf222e)' } }, error.message));
  }
  if (!ready) {
    return h('div', { className: rootClass, style: rootStyle }, h('div', { style: { aspectRatio: '4 / 3', background: 'var(--clip-editor-bg, #f6f8fa)' } }));
  }

  const f = focus;
  const shapes = slot.clip.shapes;
  const imgAttrs = {
    href: slot.image.href,
    x: slot.image.x,
    y: slot.image.y,
    width: slot.image.width,
    height: slot.image.height,
    preserveAspectRatio: slot.image.preserveAspectRatio,
  };
  const covered = isCovered(slot, view, D, 1e-4);
  const cs = f.width / 48; // 市松模様の 1 マス
  const shapePath = (s, i, extra) =>
    h('path', { key: i, d: shapeToPath(s.tag, s.attrs), transform: fmt(s.transform), clipRule: s.attrs['clip-rule'], ...extra });

  const onPointerDown = (e) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    // キー操作を受けられるように focus する
    pointerFocus.current = true;
    svgRef.current.focus({ preventScroll: true });
    pointerFocus.current = false;
    if (engine.pointerDown(e.pointerId, toC(e.clientX, e.clientY), performance.now())) {
      e.currentTarget.setPointerCapture?.(e.pointerId);
      e.preventDefault();
    }
  };
  const onPointerMove = (e) => engine.pointerMove(e.pointerId, toC(e.clientX, e.clientY), performance.now());
  const onPointerUp = (e) => engine.pointerUp(e.pointerId, performance.now(), { cancel: e.type === 'pointercancel' });

  const onKeyDown = (e) => {
    if (e.key !== 'Shift') setFocusRing(true);
    const w = engine.ctx.focusWidthC * (e.shiftKey ? 0.1 : 0.01);
    const keys = {
      ArrowLeft: () => engine.nudge(-w, 0),
      ArrowRight: () => engine.nudge(w, 0),
      ArrowUp: () => engine.nudge(0, -w),
      ArrowDown: () => engine.nudge(0, w),
      '+': () => engine.zoom(1.1, { mergeKey: true }),
      '=': () => engine.zoom(1.1, { mergeKey: true }),
      '-': () => engine.zoom(1 / 1.1, { mergeKey: true }),
      '[': () => engine.rotateBy(-1, { mergeKey: true }),
      ']': () => engine.rotateBy(1, { mergeKey: true }),
      '{': () => engine.rotateBy(-15, { mergeKey: true }),
      '}': () => engine.rotateBy(15, { mergeKey: true }),
    };
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
      e.preventDefault();
      if (e.shiftKey) engine.redo();
      else engine.undo();
      return;
    }
    const run = keys[e.key];
    if (run) {
      e.preventDefault();
      run();
    }
  };

  const editor = h(
    'svg',
    {
      ref: svgRef,
      viewBox: `${f.x} ${f.y} ${f.width} ${f.height}`,
      preserveAspectRatio: 'xMidYMid meet',
      role: 'application',
      'aria-label': labels.region,
      tabIndex: 0,
      style: {
        display: 'block',
        width: '100%',
        height: 'auto',
        aspectRatio: `${f.width} / ${f.height}`,
        touchAction: 'none',
        userSelect: 'none',
        WebkitUserSelect: 'none',
        cursor: 'grab',
        background: 'var(--clip-editor-bg, #ffffff)',
        borderRadius: 'var(--clip-editor-radius, 6px)',
        outline: 'none',
        boxShadow: focusRing ? '0 0 0 2px var(--clip-editor-focus, #0969da)' : 'none',
      },
      onFocus: () => setFocusRing(!pointerFocus.current),
      onBlur: () => setFocusRing(false),
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel: onPointerUp,
      onKeyDown,
    },
    h(
      'defs',
      null,
      h(
        'pattern',
        { id: `${uid}-checker`, patternUnits: 'userSpaceOnUse', x: f.x, y: f.y, width: cs * 2, height: cs * 2 },
        h('rect', { width: cs * 2, height: cs * 2, style: { fill: 'var(--clip-editor-checker-a, #f6f8fa)' } }),
        h('rect', { width: cs, height: cs, style: { fill: 'var(--clip-editor-checker-b, #eaeef2)' } }),
        h('rect', { x: cs, y: cs, width: cs, height: cs, style: { fill: 'var(--clip-editor-checker-b, #eaeef2)' } }),
      ),
      h('clipPath', { id: `${uid}-clip`, clipPathUnits: 'userSpaceOnUse', transform: fmt(slot.clip.Q) }, shapes.map((s, i) => shapePath(s, i))),
    ),
    h('rect', { x: f.x, y: f.y, width: f.width, height: f.height, fill: `url(#${uid}-checker)` }),
    h(
      'g',
      { transform: fmt(slot.K) },
      // ① 画像全体（半透過）
      h('image', { ...imgAttrs, transform: fmt(view), opacity: outsideOpacity }),
      // ② 枠で切り抜いた画像（不透明）
      h('g', { clipPath: `url(#${uid}-clip)` }, h('image', { ...imgAttrs, transform: fmt(view) })),
    ),
    // ③ 枠の輪郭。覆えていない間は警告色
    h(
      'g',
      { transform: fmt(slot.K), pointerEvents: 'none' },
      h(
        'g',
        { transform: fmt(slot.clip.Q), fill: 'none' },
        shapes.map((s, i) =>
          shapePath(s, i, {
            style: { stroke: covered ? 'var(--clip-editor-frame, #0969da)' : 'var(--clip-editor-warn, #cf222e)' },
            strokeWidth: 1.5,
            strokeDasharray: '6 4',
            vectorEffect: 'non-scaling-stroke',
          }),
        ),
      ),
    ),
    // ④ 当たり判定: 全面
    h('rect', { x: f.x, y: f.y, width: f.width, height: f.height, fill: 'transparent' }),
  );

  if (!toolbar) return h('div', { className: rootClass, style: rootStyle }, editor);

  const button = (label, onClick, opts = {}) =>
    h(
      'button',
      {
        type: 'button',
        onClick,
        disabled: opts.disabled,
        title: opts.title ?? label,
        'aria-label': opts.title ?? label,
        style: {
          font: 'inherit',
          fontSize: 13,
          lineHeight: '20px',
          padding: '3px 10px',
          borderRadius: 6,
          border: '1px solid var(--clip-editor-button-border, #d0d7de)',
          background: 'var(--clip-editor-button-bg, #f6f8fa)',
          color: 'var(--clip-editor-button-fg, #1f2328)',
          cursor: opts.disabled ? 'default' : 'pointer',
          opacity: opts.disabled ? 0.5 : 1,
        },
      },
      label,
    );

  return h(
    'div',
    { className: rootClass, style: rootStyle },
    editor,
    h(
      'div',
      { style: { display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' } },
      button('−', () => engine.zoom(1 / 1.1), { title: labels.zoomOut }),
      button('+', () => engine.zoom(1.1), { title: labels.zoomIn }),
      button('⟲ 90°', () => engine.rotateBy(-90), { title: labels.rotateLeft }),
      button('⟳ 90°', () => engine.rotateBy(90), { title: labels.rotateRight }),
      button(labels.fit, () => engine.fit()),
      h('span', { style: { flex: 1 } }),
      button('↶', () => engine.undo(), { title: labels.undo, disabled: !engine.canUndo() }),
      button('↷', () => engine.redo(), { title: labels.redo, disabled: !engine.canRedo() }),
      button(labels.reset, () => engine.reset()),
    ),
  );
});
