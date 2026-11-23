"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { createHash, randomUUID } = require("node:crypto");

const HISTORY_LIMIT = 200;

class Store {
  constructor(dir) {
    this.directory = dir;
    this.images = path.join(dir, "images");
    this.path = path.join(dir, "clips.json");
    fs.mkdirSync(this.images, { recursive: true });
    const data = fs.existsSync(this.path)
      ? JSON.parse(fs.readFileSync(this.path, "utf8"))
      : {};
    this.clips = data.clips ?? [];
    this.notes = data.notes ?? [];
  }

  #save(clips, notes) {
    const temporary = path.join(this.directory, `.${randomUUID()}.tmp`);
    try {
      fs.writeFileSync(temporary, JSON.stringify({ clips, notes }, null, 2), "utf8");
      fs.renameSync(temporary, this.path);
    } finally {
      fs.rmSync(temporary, { force: true });
    }
    this.clips = clips;
    this.notes = notes;
  }

  imagePath(clip) {
    return path.join(this.images, `${clip.id}.png`);
  }

  addText(text) {
    return this.#add("text", { text }, (clip) => clip.text === text);
  }

  addImage(pngBuffer) {
    const sha256 = createHash("sha256").update(pngBuffer).digest("hex");
    return this.#add("image", { sha256 }, (clip) => {
      if (clip.sha256 != null && clip.sha256 !== sha256) return false;
      try {
        return fs.readFileSync(this.imagePath(clip)).equals(pngBuffer);
      } catch (error) {
        if (error.code === "ENOENT") return false;
        throw error;
      }
    }, pngBuffer);
  }

  #add(kind, fields, matches, pngBuffer) {
    const existing = this.clips.find((clip) => clip.kind === kind && matches(clip));
    const clip = {
      ...(existing ?? { id: randomUUID().replaceAll("-", ""), kind, tags: [], pinned: false }),
      ...fields,
    };
    const newImage = !existing && kind === "image";
    const remaining = this.clips.filter((item) => item !== existing);
    const ranked = [...remaining].sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)));
    const kept = new Set(ranked.slice(0, HISTORY_LIMIT - 1));
    try {
      if (newImage) fs.writeFileSync(this.imagePath(clip), pngBuffer);
      this.#save([clip, ...remaining.filter((item) => kept.has(item))], this.notes);
    } catch (error) {
      if (newImage) this.#removeImage(clip);
      throw error;
    }
    for (const item of remaining) {
      if (!kept.has(item)) this.#removeImage(item);
    }
    return clip;
  }

  #removeImage(clip) {
    if (clip.kind === "image") {
      try {
        fs.rmSync(this.imagePath(clip), { force: true });
      } catch {
        // Image cleanup is best effort after the history has been saved.
      }
    }
  }

  updateClip(id, changes) {
    this.#save(this.clips.map((clip) => clip.id === id ? { ...clip, ...changes } : clip), this.notes);
  }

  deleteClip(id) {
    const removed = this.clips.find((clip) => clip.id === id);
    if (removed) {
      this.#save(this.clips.filter((clip) => clip !== removed), this.notes);
      this.#removeImage(removed);
    }
  }

  saveNotes(notes) {
    this.#save(this.clips, notes);
  }
}

module.exports = { Store, HISTORY_LIMIT };
