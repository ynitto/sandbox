export type Matrix = [number, number, number, number, number, number];
export type EditorMode = 'view' | 'edit';
export type EditTool = 'select' | 'pan';

export interface ImageEdit { elementId: string; matrix: Matrix }
export interface EditorDocument {
  schemaVersion: 1;
  templateId: string;
  templateRevision: string;
  images: ImageEdit[];
}
export interface EditorConstraints { minScale?: number; maxScale?: number; moveStep?: number; rotationStep?: number }
export interface EditorNotice { code: 'invalid-svg' | 'removed-content' | 'unsupported' | 'invalid-data'; message: string }
export interface SvgGeometryEditorProps {
  svg: string;
  templateId: string;
  templateRevision: string;
  mode: EditorMode;
  value?: EditorDocument;
  defaultValue?: EditorDocument;
  selectedId?: string | null;
  onSelectionChange?: (id: string | null) => void;
  onChange?: (value: EditorDocument) => void;
  onCommit?: (value: EditorDocument) => void;
  onError?: (notice: EditorNotice) => void;
  constraints?: EditorConstraints;
  className?: string;
  style?: React.CSSProperties;
}
