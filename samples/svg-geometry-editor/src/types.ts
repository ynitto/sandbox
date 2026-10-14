export type Matrix = [number, number, number, number, number, number];
export type EditorMode = 'view' | 'edit';

/** Image id → matrix in the image's parent user space. Images without an entry are unedited. */
export type Edits = Record<string, Matrix>;
export interface View { x: number; y: number; zoom: number }
export interface ChangeInfo { final: boolean }
export interface EditorNotice { code: 'invalid-svg' | 'removed-content' | 'unsupported' | 'invalid-data'; message: string }
export interface CanvasHandle { zoom(factor: number): void; resetView(): void }

export interface SvgGeometryCanvasProps {
  svg: string;
  mode: EditorMode;
  value: Edits;
  onChange: (value: Edits, info: ChangeInfo) => void;
  selectedId: string | null;
  onSelectionChange: (id: string | null) => void;
  minScale?: number;
  maxScale?: number;
  moveStep?: number;
  onViewChange?: (view: View) => void;
  onError?: (notice: EditorNotice) => void;
  className?: string;
  style?: React.CSSProperties;
}
