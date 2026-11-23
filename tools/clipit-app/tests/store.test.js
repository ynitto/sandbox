"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { Store, HISTORY_LIMIT } = require("../src/store");

function temporaryStore(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "clipit-store-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return new Store(dir);
}

test("text, metadata and notes persist, and exact duplicates move to the front", (t) => {
  const store = temporaryStore(t);
  assert.deepEqual(store.clips, []);
  const first = store.addText("日本語");
  assert.deepEqual(first, { id: first.id, kind: "text", text: "日本語", tags: [], pinned: false });
  const second = store.addText("日本語 ");
  store.updateClip(first.id, { tags: ["タグ"], pinned: true });
  const repeated = store.addText("日本語");
  assert.deepEqual(repeated, { ...first, tags: ["タグ"], pinned: true });
  assert.deepEqual(store.clips, [repeated, second]);

  const notes = [{ text: "メモ", checked: true }, { text: "次", checked: false }];
  store.saveNotes(notes);
  const reopened = new Store(store.directory);
  assert.deepEqual(reopened.clips, [repeated, second]);
  assert.deepEqual(reopened.notes, notes);
});

test("history keeps the newest clip and prefers pinned entries when trimming", (t) => {
  const store = temporaryStore(t);
  const pinned = store.addText("pinned");
  store.updateClip(pinned.id, { pinned: true });
  const image = store.addImage(Buffer.from("old image"));
  for (let i = 0; i < HISTORY_LIMIT - 1; i++) store.addText(String(i));
  assert.equal(store.clips.length, 200);
  assert.equal(store.clips[0].text, String(HISTORY_LIMIT - 2));
  assert.equal(store.clips.at(-1).id, pinned.id);
  assert.equal(store.clips.some((clip) => clip.id === image.id), false);
  assert.equal(fs.existsSync(store.imagePath(image)), false);
});

test("image deduplication checks the file bytes and deletion removes the file", (t) => {
  const store = temporaryStore(t);
  const bytes = Buffer.from([0, 128, 255]);
  const first = store.addImage(bytes);
  const text = store.addText("間");
  assert.deepEqual(store.addImage(bytes), first);
  assert.deepEqual(store.clips, [first, text]);
  const imagePath = store.imagePath(first);
  assert.deepEqual(fs.readFileSync(imagePath), bytes);

  fs.writeFileSync(imagePath, Buffer.from("different bytes"));
  const other = store.addImage(bytes);
  assert.equal(first.sha256, other.sha256);
  assert.notEqual(first.id, other.id);
  store.deleteClip(first.id);
  assert.equal(fs.existsSync(imagePath), false);
  assert.deepEqual(new Store(store.directory).clips, [other, text]);
});

test("images saved without a digest are deduplicated and get a digest", (t) => {
  const store = temporaryStore(t);
  const legacy = { id: "legacy", kind: "image", tags: [], pinned: true };
  const bytes = Buffer.from("legacy image");
  fs.writeFileSync(store.imagePath(legacy), bytes);
  fs.writeFileSync(store.path, JSON.stringify({ clips: [legacy] }), "utf8");

  const reopened = new Store(store.directory);
  const clip = reopened.addImage(bytes);
  assert.equal(clip.id, legacy.id);
  assert.equal(clip.pinned, true);
  assert.match(clip.sha256, /^[a-f0-9]{64}$/);
  assert.equal(reopened.clips.length, 1);
});
