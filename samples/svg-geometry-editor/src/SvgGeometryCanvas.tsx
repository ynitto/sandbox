import { forwardRef, useImperativeHandle } from 'react';
import { useCanvasController } from './useCanvasController';
import type { CanvasHandle, SvgGeometryCanvasProps } from './types';
import './style.css';

export const SvgGeometryCanvas = forwardRef<CanvasHandle, SvgGeometryCanvasProps>(function SvgGeometryCanvas(props, ref) {
  const c = useCanvasController(props);
  useImperativeHandle(ref, () => ({ zoom: c.zoom, resetView: c.resetView }));
  const points = c.overlay?.corners.map(p => `${p.x},${p.y}`).join(' ') ?? '';
  return <div
    ref={c.hostRef} className={`sge-canvas ${props.className ?? ''}`} style={props.style} data-mode={props.mode}
    tabIndex={0} role="application" aria-label="SVG image editor canvas"
    onPointerDown={c.onPointerDown} onPointerMove={c.onPointerMove}
    onPointerUp={c.onPointerUp} onPointerCancel={c.onPointerCancel}
    onLostPointerCapture={c.onPointerCancel}
    onKeyDown={c.onKeyDown}
  >
    {c.prepared.svg ? <div ref={c.sceneRef} className="sge-scene" style={{ transform: `translate(${c.view.x}px, ${c.view.y}px) scale(${c.view.zoom})` }} dangerouslySetInnerHTML={{ __html: c.prepared.svg.markup }} /> : <div className="sge-error">SVGを表示できません</div>}
    {props.mode === 'edit' && c.overlay && <svg className="sge-overlay" aria-hidden="true">
      <polygon points={points} className="sge-selection" data-editor-id={c.selection ?? undefined} />
      <line x1={(c.overlay.corners[0].x + c.overlay.corners[1].x) / 2} y1={(c.overlay.corners[0].y + c.overlay.corners[1].y) / 2} x2={c.overlay.rotation.x} y2={c.overlay.rotation.y} className="sge-stem" />
      {c.overlay.corners.map((p, i) => <g key={i} data-handle={i}>
        <circle cx={p.x} cy={p.y} r="22" className="sge-hit" />
        <circle cx={p.x} cy={p.y} r="7" className="sge-knob" />
      </g>)}
      <g data-handle="rotate"><circle cx={c.overlay.rotation.x} cy={c.overlay.rotation.y} r="22" className="sge-hit" /><circle cx={c.overlay.rotation.x} cy={c.overlay.rotation.y} r="8" className="sge-knob" /></g>
    </svg>}
  </div>;
});
