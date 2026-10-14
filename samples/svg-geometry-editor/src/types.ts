export type Matrix = [number, number, number, number, number, number];
export type EditorMode = 'view' | 'edit';

/** Editable element id → matrix in the element's parent user space. Elements without an entry are unedited. */
export type Edits = Record<string, Matrix>;
export interface View { x: number; y: number; zoom: number }
export interface ChangeInfo { final: boolean }
export interface EditorNotice { code: 'invalid-svg' | 'removed-content' | 'unsupported' | 'invalid-data'; message: string }
export interface SanitizeRules {
  /** SVG element names allowed in addition to the defaults. Script-capable and animation elements stay blocked. */
  tags?: readonly string[];
  /** Attribute names allowed in addition to the defaults. Event handlers (`on*`) stay blocked. */
  attributes?: readonly string[];
  /** Which elements with an `id` can be selected and moved. Receives the sanitized element and its original id. */
  isEditable?: (element: Element, id: string) => boolean;
}
export interface CanvasHandle { zoom(factor: number): void; resetView(): void }

export interface SvgGeometryCanvasProps {
  svg: string;
  /** Read again only when its identity changes, so define it outside render. */
  rules?: SanitizeRules;
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
