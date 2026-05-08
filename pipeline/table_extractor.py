"""Table Transformer wrapper (microsoft/table-transformer-*)."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import warnings

from PIL import Image

# DETR label maps for the two Table Transformer models
_DETECT_LABELS = {
    0: "table",
    1: "table rotated",
}
_STRUCT_LABELS = {
    0: "table",
    1: "table column",
    2: "table row",
    3: "table column header",
    4: "table projected row header",
    5: "table spanning cell",
    6: "no object",
}

_DETECT_MODEL_ID = "microsoft/table-transformer-detection"
_STRUCT_MODEL_ID = "microsoft/table-transformer-structure-recognition"


@dataclass
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    def crop(self, image: Image.Image) -> Image.Image:
        w, h = image.size
        box = (
            max(0, int(self.x0 * w)),
            max(0, int(self.y0 * h)),
            min(w, int(self.x1 * w)),
            min(h, int(self.y1 * h)),
        )
        return image.crop(box)


@dataclass
class DetectedTable:
    bbox: BBox
    score: float
    cropped: Image.Image


@dataclass
class TableStructure:
    """Row/column grid inferred from structure-recognition output."""
    rows: list[BBox]       # relative to cropped image
    columns: list[BBox]
    header_rows: list[int]  # indices into rows that are headers


class TableTransformerExtractor:
    def __init__(self, device: str = "cpu", threshold: float = 0.9):
        self.device = device
        self.threshold = threshold
        self._detect_pipeline = None
        self._struct_pipeline = None

    def _load_detect(self):
        if self._detect_pipeline is None:
            from transformers import AutoImageProcessor, TableTransformerForObjectDetection
            import torch
            self._detect_proc = AutoImageProcessor.from_pretrained(_DETECT_MODEL_ID)
            self._detect_model = TableTransformerForObjectDetection.from_pretrained(
                _DETECT_MODEL_ID
            ).to(self.device)
            self._detect_model.eval()
            self._torch = torch
            self._detect_pipeline = True

    def _load_struct(self):
        if self._struct_pipeline is None:
            from transformers import AutoImageProcessor, TableTransformerForObjectDetection
            self._struct_proc = AutoImageProcessor.from_pretrained(_STRUCT_MODEL_ID)
            self._struct_model = TableTransformerForObjectDetection.from_pretrained(
                _STRUCT_MODEL_ID
            ).to(self.device)
            self._struct_model.eval()
            self._struct_pipeline = True

    def detect_tables(self, image: Image.Image) -> list[DetectedTable]:
        """Find table regions in a page image."""
        self._load_detect()
        import torch

        rgb = image.convert("RGB")
        inputs = self._detect_proc(images=rgb, return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self._detect_model(**inputs)

        target_sizes = torch.tensor([[rgb.height, rgb.width]])
        results = self._detect_proc.post_process_object_detection(
            outputs, threshold=self.threshold, target_sizes=target_sizes
        )[0]

        detected: list[DetectedTable] = []
        for score, label, box in zip(
            results["scores"], results["labels"], results["boxes"]
        ):
            lbl = _DETECT_LABELS.get(label.item(), "")
            if "table" not in lbl:
                continue
            x0, y0, x1, y1 = box.tolist()
            # normalize to [0,1]
            bbox = BBox(
                x0 / rgb.width, y0 / rgb.height,
                x1 / rgb.width, y1 / rgb.height,
            )
            cropped = bbox.crop(rgb)
            detected.append(DetectedTable(bbox=bbox, score=score.item(), cropped=cropped))
        return detected

    def recognize_structure(self, table_image: Image.Image) -> TableStructure:
        """Extract rows/columns from a cropped table image."""
        self._load_struct()
        import torch

        rgb = table_image.convert("RGB")
        inputs = self._struct_proc(images=rgb, return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self._struct_model(**inputs)

        target_sizes = torch.tensor([[rgb.height, rgb.width]])
        results = self._struct_proc.post_process_object_detection(
            outputs, threshold=0.7, target_sizes=target_sizes
        )[0]

        rows: list[BBox] = []
        columns: list[BBox] = []
        header_rows: list[int] = []

        for score, label, box in zip(
            results["scores"], results["labels"], results["boxes"]
        ):
            lbl = _STRUCT_LABELS.get(label.item(), "")
            x0, y0, x1, y1 = box.tolist()
            bbox = BBox(
                x0 / rgb.width, y0 / rgb.height,
                x1 / rgb.width, y1 / rgb.height,
            )
            if lbl == "table row":
                rows.append(bbox)
            elif lbl == "table column":
                columns.append(bbox)
            elif lbl == "table column header":
                # column header spans all columns; mark the y-range as header
                header_rows.append(len(rows))  # approximate

        rows.sort(key=lambda b: b.y0)
        columns.sort(key=lambda b: b.x0)

        return TableStructure(rows=rows, columns=columns, header_rows=header_rows)

    def extract_cell_text(
        self,
        table_image: Image.Image,
        structure: TableStructure,
        ocr_fn=None,
    ) -> list[list[str]]:
        """
        Extract cell text by cropping row×column intersections.
        If ocr_fn is None, returns empty strings (caller supplies OCR or
        uses pdfplumber-based text extraction instead).
        """
        grid: list[list[str]] = []
        for row_bbox in structure.rows:
            row_cells: list[str] = []
            for col_bbox in structure.columns:
                cell_bbox = BBox(
                    x0=max(row_bbox.x0, col_bbox.x0),
                    y0=max(row_bbox.y0, col_bbox.y0),
                    x1=min(row_bbox.x1, col_bbox.x1),
                    y1=min(row_bbox.y1, col_bbox.y1),
                )
                cell_img = cell_bbox.crop(table_image)
                text = ocr_fn(cell_img) if ocr_fn else ""
                row_cells.append(text.strip())
            grid.append(row_cells)
        return grid
