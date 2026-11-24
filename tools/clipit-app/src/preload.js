"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("clipit", {
  getState: () => ipcRenderer.invoke("state:get"),
  addText: (text) => ipcRenderer.invoke("clips:add-text", { text }),
  addImage: (pngBase64) => ipcRenderer.invoke("clips:add-image", { pngBase64 }),
  getImage: (id) => ipcRenderer.invoke("clips:image", { id }),
  updateClip: (id, changes) => ipcRenderer.invoke("clips:update", { id, changes }),
  deleteClip: (id) => ipcRenderer.invoke("clips:delete", { id }),
  saveNotes: (notes) => ipcRenderer.invoke("notes:save", { notes }),
  copyNote: (text) => ipcRenderer.invoke("notes:copy", { text }),
  paste: (id) => ipcRenderer.invoke("clips:paste", { id }),
  translate: (text) => ipcRenderer.invoke("text:translate", { text }),
  ocr: (id) => ipcRenderer.invoke("clips:ocr", { id }),
  evaluate: (expression) => ipcRenderer.invoke("calc:evaluate", { expression }),
  listCommands: () => ipcRenderer.invoke("devkit:list"),
  runCommand: (name, argv, stdin) => ipcRenderer.invoke("devkit:run", { name, argv, stdin }),
  hide: () => ipcRenderer.invoke("panel:hide"),
  quit: () => ipcRenderer.invoke("app:quit"),
  onClipsChanged: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("clips:changed", listener);
    return () => ipcRenderer.removeListener("clips:changed", listener);
  },
});
