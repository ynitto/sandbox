'use strict';

const flowModel = require('./flow-model');

function compile({ workflowId = '', title = '', understanding = {}, candidate = {} } = {}) {
  const source = candidate && typeof candidate === 'object' ? candidate : {};
  const purpose = String(understanding.purpose || '').trim();
  const workflow = {
    version: 2,
    id: String(workflowId || source.id || '').trim(),
    name: String(title || source.name || '').trim(),
    description: String(source.description || purpose).trim(),
    purpose: 'implementation',
    entry: [], exit: [],
    nodes: Array.isArray(source.nodes) ? source.nodes : [],
    ...(Array.isArray(source.rework) && source.rework.length ? { rework: source.rework } : {}),
  };
  const preview = flowModel.preview(workflow);
  return { ...preview, workflow: preview.workflow, digest: preview.digest, preview };
}

module.exports = { compile };
