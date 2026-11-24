"use strict";

const api = window.clipit;
const byId = (id) => document.getElementById(id);
const tabs = [...document.querySelectorAll('[role="tab"]')];
const historyList = byId("clips");
const preview = byId("preview");
const tags = byId("tags");
const tagForm = byId("tag-form");
const canvas = byId("canvas");
const paint = canvas.getContext("2d");
let clips = [];
let notes = [];
let visibleClips = [];
let selectedId = null;
let editingId = null;
let savingNotes = false;
let activeTab = "history";
let commands = [];
let toolResult;
let pointer = null;
let refreshing = false;
let refreshPending = false;
let lastError;
const running = new Set();

function status(message) {
  byId("status").textContent = message;
}

async function request(response) {
  const result = await response;
  if (result?.ok === false) throw new Error(result.error);
  return result;
}

function perform(action) {
  return async (event) => {
    event?.preventDefault();
    try {
      await action(event);
    } catch (error) {
      status(error.message);
    }
  };
}

function selectTab(id) {
  activeTab = id;
  for (const tab of tabs) {
    const panel = byId(tab.getAttribute("aria-controls"));
    const selected = panel.id === id;
    tab.setAttribute("aria-selected", selected);
    tab.tabIndex = selected ? 0 : -1;
    panel.hidden = !selected;
  }
}

for (const [index, tab] of tabs.entries()) {
  tab.onclick = () => selectTab(tab.getAttribute("aria-controls"));
  tab.onkeydown = (event) => {
    const offset = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (!offset) return;
    event.preventDefault();
    const next = tabs[(index + offset + tabs.length) % tabs.length];
    next.click();
    next.focus();
  };
}

byId("hide").onclick = perform(() => request(api.hide()));
byId("quit").onclick = perform(() => request(api.quit()));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    void perform(() => request(api.hide()))();
  }
});

async function imageSource(id) {
  const png = await request(api.getImage(id));
  return "data:image/png;base64," + png;
}

const thumbnails = new IntersectionObserver((entries) => {
  for (const { target, isIntersecting } of entries) {
    if (!isIntersecting) continue;
    thumbnails.unobserve(target);
    imageSource(target.dataset.clipId).then((source) => { target.src = source; }).catch(() => {
      target.remove();
    });
  }
}, { root: historyList });

function selectedClip() {
  return visibleClips.find((clip) => clip.id === selectedId);
}

function updateActions() {
  const clip = selectedClip();
  byId("edit-tags").disabled = byId("pin").disabled = byId("delete-clip").disabled = !clip;
  byId("pin").textContent = clip?.pinned ? "ピン解除" : "ピン留め";
  byId("translate").disabled = clip?.kind !== "text" || running.has("translate");
  byId("ocr").disabled = clip?.kind !== "image" || running.has("ocr");
  byId("open-paint").disabled = clip?.kind !== "image";
}

function closeTags() {
  editingId = null;
  tagForm.hidden = true;
}

function selectClip(id, scroll = false) {
  selectedId = id;
  if (editingId && editingId !== id) closeTags();
  for (const row of historyList.children) {
    const selected = row.dataset.id === id;
    row.setAttribute("aria-selected", selected);
    if (selected && scroll) row.scrollIntoView({ block: "nearest" });
  }
  if (id) historyList.setAttribute("aria-activedescendant", "clip-" + id);
  else historyList.removeAttribute("aria-activedescendant");

  const clip = selectedClip();
  const previewKey = clip?.kind === "image" ? clip.id : null;
  if (previewKey !== preview.dataset.image || !previewKey) {
    preview.textContent = clip?.kind === "text" ? clip.text : "";
    preview.dataset.image = previewKey ?? "";
    if (previewKey) {
      const image = new Image();
      image.alt = "クリップ画像";
      preview.append(image);
      imageSource(id).then((source) => { image.src = source; }).catch(() => {
        if (image.isConnected) preview.textContent = "画像ファイルを読み込めません。";
      });
    }
  }
  updateActions();
}

function renderHistory(preferredId = selectedId) {
  const tag = tags.value;
  const labels = [...new Set(clips.flatMap((clip) => clip.tags))].sort((a, b) => a.localeCompare(b, "ja"));
  tags.replaceChildren(new Option("すべてのタグ", ""), ...labels.map((label) => new Option(label, label)));
  tags.value = labels.includes(tag) ? tag : "";
  const query = byId("search").value.trim().toLowerCase();
  const ordered = byId("order").value === "oldest" ? [...clips].reverse() : [...clips];
  visibleClips = ordered.filter((clip) =>
    (!tags.value || clip.tags.includes(tags.value)) &&
    [clip.text ?? "", ...clip.tags].join(" ").toLowerCase().includes(query),
  ).sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)));

  thumbnails.disconnect();
  historyList.replaceChildren();
  for (const clip of visibleClips) {
    const row = document.createElement("div");
    row.className = "clip";
    row.id = "clip-" + clip.id;
    row.dataset.id = clip.id;
    row.setAttribute("role", "option");
    if (clip.kind === "image") {
      const thumbnail = new Image();
      thumbnail.alt = "";
      thumbnail.dataset.clipId = clip.id;
      row.append(thumbnail);
      thumbnails.observe(thumbnail);
    }
    const label = document.createElement("span");
    const summary = clip.kind === "image" ? "画像" : [...clip.text.replace(/\s+/g, " ").trim()].slice(0, 100).join("");
    label.textContent = (clip.pinned ? "📌 " : "") + summary +
      (clip.tags.length ? "\n" + clip.tags.join(", ") : "");
    row.append(label);
    row.onclick = () => {
      selectClip(clip.id);
      historyList.focus({ preventScroll: true });
    };
    row.ondblclick = perform(() => request(api.paste(clip.id)));
    historyList.append(row);
  }
  const id = visibleClips.find((clip) => clip.id === preferredId)?.id ?? visibleClips[0]?.id ?? null;
  selectClip(id, Boolean(id && activeTab === "history"));
}

byId("search").oninput = tags.onchange = byId("order").onchange = () => renderHistory();
historyList.onkeydown = performHistoryKey;

function performHistoryKey(event) {
  if (event.isComposing) return;
  if (event.key === "Enter" && selectedId) {
    void perform(() => request(api.paste(selectedId)))(event);
  }
  const step = { ArrowUp: -1, ArrowDown: 1 }[event.key];
  if (!step || !visibleClips.length) return;
  event.preventDefault();
  const current = visibleClips.findIndex((clip) => clip.id === selectedId);
  const index = Math.max(0, Math.min(visibleClips.length - 1, current + step));
  selectClip(visibleClips[index].id, true);
}

byId("edit-tags").onclick = () => {
  const clip = selectedClip();
  if (!clip) return;
  editingId = clip.id;
  byId("tag-input").value = clip.tags.join(", ");
  tagForm.hidden = false;
  byId("tag-input").focus();
};
byId("cancel-tags").onclick = closeTags;
tagForm.onsubmit = perform(async () => {
  const id = editingId;
  await request(api.updateClip(id, { tags: byId("tag-input").value.split(",") }));
  if (editingId === id) closeTags();
});
byId("pin").onclick = perform(() => {
  const clip = selectedClip();
  if (!clip) return;
  return request(api.updateClip(clip.id, { pinned: !clip.pinned }));
});
byId("delete-clip").onclick = perform(() => request(api.deleteClip(selectedId)));

async function saveText(text, message, reveal = false) {
  const clip = await request(api.addText(text));
  if (reveal) {
    byId("search").value = "";
    tags.value = "";
    selectTab("history");
  }
  await refresh(clip.id);
  status(message);
}

for (const [id, label] of [["translate", "翻訳"], ["ocr", "文字読み取り"]]) {
  byId(id).onclick = perform(async () => {
    const clip = selectedClip();
    running.add(id);
    updateActions();
    status(label + "を実行しています…");
    try {
      const text = await request(id === "translate" ? api.translate(clip.text) : api.ocr(clip.id));
      await saveText(text, label + "の結果を履歴に追加しました。", true);
      historyList.focus();
    } finally {
      running.delete(id);
      updateActions();
    }
  });
}

function clearPaint() {
  paint.fillStyle = "#fff";
  paint.fillRect(0, 0, canvas.width, canvas.height);
}

function point(event) {
  const bounds = canvas.getBoundingClientRect();
  const scale = Math.min(bounds.width / canvas.width, bounds.height / canvas.height);
  return {
    x: (event.clientX - bounds.left - (bounds.width - canvas.width * scale) / 2) / scale,
    y: (event.clientY - bounds.top - (bounds.height - canvas.height * scale) / 2) / scale,
  };
}

function stroke(from, to) {
  paint.strokeStyle = paint.fillStyle = byId("color").value;
  paint.lineWidth = Number(byId("width").value);
  paint.lineCap = "round";
  paint.beginPath();
  if (from.x === to.x && from.y === to.y) {
    paint.arc(to.x, to.y, paint.lineWidth / 2, 0, Math.PI * 2);
    paint.fill();
  } else {
    paint.moveTo(from.x, from.y);
    paint.lineTo(to.x, to.y);
    paint.stroke();
  }
}

canvas.onpointerdown = (event) => {
  if (pointer || event.button !== 0) return;
  const start = point(event);
  if (start.x < 0 || start.y < 0 || start.x >= canvas.width || start.y >= canvas.height) return;
  canvas.setPointerCapture(event.pointerId);
  pointer = { id: event.pointerId, point: start };
  stroke(start, start);
};
canvas.onpointermove = (event) => {
  if (pointer?.id !== event.pointerId) return;
  const next = point(event);
  stroke(pointer.point, next);
  pointer.point = next;
};
canvas.onpointerup = canvas.onpointercancel = canvas.onlostpointercapture = (event) => {
  if (pointer?.id === event.pointerId) pointer = null;
};
byId("clear-paint").onclick = clearPaint;
byId("save-paint").onclick = perform(async () => {
  const png = canvas.toDataURL("image/png").split(",")[1];
  const clip = await request(api.addImage(png));
  await refresh(clip.id);
  status("画像を履歴に追加しました。");
});
byId("open-paint").onclick = perform(async () => {
  const source = await imageSource(selectedId);
  const image = new Image();
  image.src = source;
  await image.decode();
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  clearPaint();
  paint.drawImage(image, 0, 0);
  selectTab("paint");
});
clearPaint();

function renderNotes() {
  const list = byId("note-list");
  list.replaceChildren();
  for (const [index, note] of notes.entries()) {
    const row = document.createElement("div");
    row.className = "row note-row";
    const label = document.createElement("label");
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = note.checked;
    check.disabled = savingNotes;
    check.onchange = perform(() => saveNotes(notes.map((item, position) =>
      position === index ? { ...item, checked: check.checked } : item,
    )));
    const text = document.createElement("span");
    text.className = "note-text";
    text.textContent = note.text;
    label.append(check, text);
    const copy = document.createElement("button");
    copy.textContent = "コピー";
    copy.onclick = perform(async () => {
      const clip = await request(api.copyNote(note.text));
      await refresh(clip.id);
      status("クリップボードにコピーしました。");
    });
    const remove = document.createElement("button");
    remove.textContent = "削除";
    remove.disabled = savingNotes;
    remove.onclick = perform(() => saveNotes(notes.filter((_, position) => position !== index)));
    row.append(label, copy, remove);
    list.append(row);
  }
}

async function saveNotes(next) {
  if (savingNotes) return;
  savingNotes = true;
  byId("note-form").querySelector("button").disabled = true;
  renderNotes();
  try {
    await request(api.saveNotes(next));
    notes = next;
  } finally {
    savingNotes = false;
    byId("note-form").querySelector("button").disabled = false;
    renderNotes();
  }
}

byId("note-form").onsubmit = perform(async () => {
  const input = byId("note-input");
  const text = input.value.trim();
  if (!text || savingNotes) return;
  const value = input.value;
  await saveNotes([...notes, { text, checked: false }]);
  if (input.value === value) input.value = "";
});

byId("expression").oninput = () => { byId("answer").textContent = ""; };
async function calculate(save) {
  const expression = byId("expression").value.trim();
  let equation;
  try {
    const result = await request(api.evaluate(expression));
    equation = expression + " = " + Number(result.toPrecision(15));
  } catch (error) {
    if (byId("expression").value.trim() === expression) byId("answer").textContent = error.message;
    return;
  }
  if (byId("expression").value.trim() === expression) byId("answer").textContent = equation;
  if (save) await saveText(equation, "計算結果を履歴に追加しました。");
}
byId("calc-form").onsubmit = perform(() => calculate(false));
byId("save-calc").onclick = perform(() => calculate(true));

function splitArguments(text) {
  const args = [];
  let token = "";
  let quote = "";
  let started = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    const next = text[i + 1];
    if (char === "\\" && quote !== "'" && next &&
        (next === quote || (!quote && (/\s/.test(next) || next === '"' || next === "'")))) {
      token += next;
      i++;
      started = true;
    } else if (quote) {
      if (char === quote) quote = "";
      else token += char;
    } else if (char === '"' || char === "'") {
      quote = char;
      started = true;
    } else if (/\s/.test(char)) {
      if (started) args.push(token);
      token = "";
      started = false;
    } else {
      token += char;
      started = true;
    }
  }
  if (quote) throw new Error("引数のクォートが閉じていません。");
  if (started) args.push(token);
  return args;
}

function updateUsage() {
  const name = byId("command").value;
  const command = commands.find((command) => command.name === name);
  const example = command?.example ?? "";
  byId("usage").textContent = command?.usage ?? "";
  byId("tool-example").textContent = example;
  byId("tool-example-row").hidden = !example;
}

byId("command").onchange = updateUsage;
byId("fill-example").onclick = () => {
  const select = byId("command");
  const command = commands.find(({ name }) => name === select.value);
  if (!command?.example) return;

  const invocation = `devkit ${command.name}`;
  const prefix = invocation + " ";
  if (command.example !== invocation && !command.example.startsWith(prefix)) return;

  select.value = command.name;
  byId("argv").value = command.example.slice(prefix.length);
};
byId("tool-form").onsubmit = perform(async () => {
  if (byId("run-tool").disabled) return;
  const argv = splitArguments(byId("argv").value);
  byId("run-tool").disabled = true;
  byId("save-tool").disabled = true;
  toolResult = null;
  byId("tool-output").hidden = true;
  status("コマンドを実行しています…");
  try {
    toolResult = await request(api.runCommand(byId("command").value, argv, byId("stdin").value));
    byId("exit-code").textContent = toolResult.code;
    byId("stdout").textContent = toolResult.stdout;
    byId("stderr").textContent = toolResult.stderr;
    byId("tool-output").hidden = false;
    byId("save-tool").disabled = !toolResult.stdout;
    status("");
  } finally {
    byId("run-tool").disabled = false;
  }
});
byId("save-tool").onclick = perform(() => saveText(toolResult.stdout, "標準出力を履歴に追加しました。"));

async function loadCommands() {
  try {
    const result = await request(api.listCommands());
    commands = result.commands;
    byId("tool-reason").textContent = result.reason ?? (commands.length ? "" : "実行できるコマンドがありません。");
    byId("command").replaceChildren(...commands.map(({ name, help }) => new Option(name + " — " + help, name)));
    byId("tool-fields").disabled = !commands.length;
    updateUsage();
  } catch (error) {
    byId("tool-reason").textContent = error.message;
  }
}

let preferredClip;
async function refresh(preferredId) {
  if (preferredId) preferredClip = preferredId;
  refreshPending = true;
  if (refreshing) return;
  refreshing = true;
  try {
    while (refreshPending) {
      refreshPending = false;
      const state = await request(api.getState());
      if (preferredClip || JSON.stringify(state.clips) !== JSON.stringify(clips) || !tags.options.length) {
        clips = state.clips;
        renderHistory(preferredClip);
        preferredClip = null;
      }
      if (!savingNotes && JSON.stringify(state.notes) !== JSON.stringify(notes)) {
        notes = state.notes;
        renderNotes();
      }
      if (state.notice) status(state.notice);
      else if (state.lastError && lastError !== state.lastError) status(state.lastError);
      lastError = state.lastError;
    }
  } catch (error) {
    status(error.message);
  } finally {
    refreshing = false;
  }
}

updateActions();
api.onClipsChanged(() => { void refresh(); });
void refresh();
void loadCommands();
