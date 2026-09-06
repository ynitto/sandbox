'use strict';

const fs = require('node:fs');
const { randomUUID } = require('node:crypto');
const store = require('./store');
const teachingStore = require('./teaching-store');

function stage(root, machine, generationId = '', options = {}) {
  const session = teachingStore.load(root, machine);
  const id = String(generationId || session.activeGenerationId || '').trim();
  const generation = session.generations.find((item) => item.id === id);
  if (!generation || !generation.makerSpec) throw new Error('試運転する候補が見つかりません');
  const suffix = String(options.id || randomUUID()).toLowerCase().replace(/[^a-z0-9-]/g, '').slice(0, 40);
  if (!suffix) throw new Error('試運転の識別子が不正です');
  const trialMachine = `trial-${suffix}`;
  store.save(root, { ...generation.makerSpec, machine: trialMachine, name: `試運転: ${generation.makerSpec.name || session.title}` });
  fs.writeFileSync(`${store.machineDir(root, trialMachine)}/.teaching-trial`, `${machine}\n`, 'utf8');
  return { trialMachine, generationId: id, jobSpec: generation.jobSpec || {} };
}

function cleanup(root, trialMachine) {
  const name = String(trialMachine || '').trim();
  if (!/^trial-[a-z0-9][a-z0-9-]{0,39}$/.test(name)) throw new Error('一時ワークフローだけを片付けられます');
  const dir = store.machineDir(root, name);
  fs.rmSync(dir, { recursive: true, force: true });
  return { removed: name };
}

module.exports = { stage, cleanup };
