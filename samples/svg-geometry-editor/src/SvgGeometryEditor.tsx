import { SvgCanvas } from './SvgCanvas';
import { EditorPanel, EditorToolbar } from './EditorControls';
import { useEditorController } from './useEditorController';
import type { SvgGeometryEditorProps } from './types';
import './style.css';

export function SvgGeometryEditor(props: SvgGeometryEditorProps) {
  const c = useEditorController(props);
  return <div className={`sge-root ${props.className ?? ''}`} style={props.style}>
    <EditorToolbar c={c} mode={props.mode} />
    <div className="sge-workspace"><SvgCanvas controller={c} mode={props.mode} />{props.mode === 'edit' && <EditorPanel c={c} />}</div>
  </div>;
}
