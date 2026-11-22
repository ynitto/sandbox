"""Persistent clipboard history and notes."""

import json
import os
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

HISTORY_LIMIT = 200


class Store:
    def __init__(self, directory=None):
        local = os.environ.get("LOCALAPPDATA")
        self.directory = Path(directory) if directory is not None else (
            Path(local) / "ClipIt" if local else Path("data")
        )
        self.images = self.directory / "images"
        self.images.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "clips.json"
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.clips = data.get("clips", [])
        self.notes = data.get("notes", [])

    def _save(self, clips, notes):
        temporary = self.directory / f".{uuid4().hex}.tmp"
        try:
            temporary.write_text(
                json.dumps({"clips": clips, "notes": notes}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.clips, self.notes = clips, notes

    def image_path(self, clip):
        return self.images / f"{clip['id']}.png"

    def add_text(self, text):
        return self._add("text", text)

    def add_image(self, png):
        return self._add("image", png)

    def _add(self, kind, value):
        digest = sha256(value).hexdigest() if kind == "image" else None
        existing = None
        for clip in self.clips:
            if clip["kind"] != kind:
                continue
            if kind == "text":
                matches = clip["text"] == value
            elif clip.get("sha256") not in (None, digest):
                matches = False
            else:
                try:
                    matches = self.image_path(clip).read_bytes() == value
                except FileNotFoundError:
                    matches = False
            if matches:
                existing = clip
                break

        clip = existing if existing is not None else {
            "id": uuid4().hex, "kind": kind, "tags": [], "pinned": False,
        }
        if kind == "text":
            clip = {**clip, "text": value}
        else:
            clip = {**clip, "sha256": digest}
        new_image = existing is None and kind == "image"
        ordered = [clip] + [item for item in self.clips if item is not existing]
        kept = [clip, *sorted(ordered[1:], key=lambda item: not item["pinned"])[:HISTORY_LIMIT - 1]]
        kept_ids = {item["id"] for item in kept}
        try:
            if new_image:
                self.image_path(clip).write_bytes(value)
            self._save([item for item in ordered if item["id"] in kept_ids], self.notes)
        except Exception:
            if new_image:
                self._remove_image(clip)
            raise
        for item in ordered:
            if item["id"] not in kept_ids:
                self._remove_image(item)
        return clip

    def _remove_image(self, clip):
        if clip["kind"] == "image":
            try:
                self.image_path(clip).unlink(missing_ok=True)
            except OSError:
                pass

    def update_clip(self, clip_id, **changes):
        self._save([
            {**clip, **changes} if clip["id"] == clip_id else clip
            for clip in self.clips
        ], self.notes)

    def delete_clip(self, clip_id):
        removed = next((clip for clip in self.clips if clip["id"] == clip_id), None)
        if removed is not None:
            self._save([clip for clip in self.clips if clip is not removed], self.notes)
            self._remove_image(removed)

    def save_notes(self, notes):
        self._save(self.clips, notes)
