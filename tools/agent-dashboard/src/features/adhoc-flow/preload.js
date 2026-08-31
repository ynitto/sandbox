'use strict';

function designRefOnly(raw) {
  const value = raw && typeof raw === 'object' ? raw : {};
  const source = value.flow && typeof value.flow === 'object' ? value.flow
    : value.snapshot && typeof value.snapshot === 'object' ? value.snapshot : value;
  const origin = source.origin && typeof source.origin === 'object' ? source.origin : {};
  const id = source.id;
  if (id == null) return raw;
  return {
    id: String(id),
    scope: String(source.scope || source._scope || origin.scope || ''),
    repository: String(source.repository || source._repository || source.cwd || origin.repository || ''),
  };
}

function preparationPayload(payload) {
  const out = { ...(payload && typeof payload === 'object' ? payload : {}) };
  const cleanDesign = (design) => {
    if (!design || typeof design !== 'object') return design;
    if (design.flow && typeof design.flow === 'object') return { ...design, flow: designRefOnly(design.flow) };
    if (design.selection && typeof design.selection === 'object') {
      return { ...design, selection: designRefOnly(design.selection) };
    }
    return designRefOnly(design);
  };
  if (out.design) out.design = cleanDesign(out.design);
  if (out.designFlow) out.designFlow = designRefOnly(out.designFlow);
  if (out.designSelection) out.designSelection = designRefOnly(out.designSelection);
  if (Array.isArray(out.candidates)) {
    out.candidates = out.candidates.map((candidate) => ({
      ...candidate,
      ...(candidate && candidate.design ? { design: cleanDesign(candidate.design) } : {}),
      ...(candidate && candidate.designFlow ? { designFlow: designRefOnly(candidate.designFlow) } : {}),
      ...(candidate && candidate.designSelection
        ? { designSelection: designRefOnly(candidate.designSelection) } : {}),
    }));
  }
  return out;
}

function designPreviewPayload(payload) {
  const source = payload && typeof payload === 'object' ? payload : {};
  const candidate = source.selection || source.designFlow || source.flow || source;
  const ref = designRefOnly(candidate);
  return {
    ...(source.mode ? { mode: source.mode } : {}),
    ...(ref && ref.id != null ? ref : {}),
  };
}

module.exports = {
  adhocFlowOverview: (invoke) => (payload) => invoke('adhocFlow:overview', payload || {}),
  adhocFlowDesignCatalog: (invoke) => (payload) => invoke('adhocFlow:designCatalog', payload || {}),
  adhocFlowRun: (invoke) => (payload) => invoke('adhocFlow:run', payload || {}),
  adhocFlowInteractionResponse: (invoke) => (payload) => invoke('adhocFlow:interactionResponse', payload || {}),
  adhocFlowSubmit: (invoke) => (payload) => invoke('adhocFlow:submit', payload || {}),
  // 編集付き再実行。plan は渡さず、フロー選択（id / scope / repository）だけを送る
  // ——定義の解決と plan 化は main が行う（他の投入口と同じ規則）。
  adhocFlowResubmit: (invoke) => (payload) => {
    const value = { ...(payload && typeof payload === 'object' ? payload : {}) };
    if (value.edits && typeof value.edits === 'object') {
      const edits = { ...value.edits };
      delete edits.plan;
      if (edits.selection && typeof edits.selection === 'object') {
        edits.selection = { ...edits.selection, ...designRefOnly(edits.selection) };
        delete edits.selection.definition;
        delete edits.selection.plan;
      }
      value.edits = edits;
    }
    return invoke('adhocFlow:resubmit', value);
  },
  adhocFlowRunInput: (invoke) => (payload) => invoke('adhocFlow:runInput', payload || {}),
  adhocFlowDistillSession: (invoke) => (payload) => invoke('adhocFlow:distillSession', payload || {}),
  adhocFlowDistillRun: (invoke) => (payload) => invoke('adhocFlow:distillRun', payload || {}),
  adhocFlowSaveDistilled: (invoke) => (payload) => invoke('adhocFlow:saveDistilled', payload || {}),
  adhocFlowBatchPreview: (invoke) => (payload) => invoke('adhocFlow:batchPreview', payload || {}),
  adhocFlowBatchSubmit: (invoke) => (payload) => invoke('adhocFlow:batchSubmit', payload || {}),
  adhocFlowCancel: (invoke) => (payload) => invoke('adhocFlow:cancel', payload || {}),
  adhocFlowForceComplete: (invoke) => (payload) => invoke('adhocFlow:forceComplete', payload || {}),
  adhocFlowDeleteRun: (invoke) => (payload) => invoke('adhocFlow:deleteRun', payload || {}),
  adhocFlowSavePreset: (invoke) => (payload) => invoke('adhocFlow:savePreset', payload || {}),
  adhocFlowDeletePreset: (invoke) => (payload) => invoke('adhocFlow:deletePreset', payload || {}),
  adhocFlowSaveWorkflow: (invoke) => (payload) => invoke('adhocFlow:saveWorkflow', payload || {}),
  adhocFlowDeleteWorkflow: (invoke) => (payload) => invoke('adhocFlow:deleteWorkflow', payload || {}),
  adhocFlowCopyMethod: (invoke) => (payload) => invoke('adhocFlow:copyMethod', payload || {}),
  adhocFlowSnapshotSelection: (invoke) => (payload) => {
    const value = { ...(payload && typeof payload === 'object' ? payload : {}) };
    if (value.selection && typeof value.selection === 'object' && value.selection.type === 'custom') {
      value.selection = { ...value.selection, ...designRefOnly(value.selection) };
      delete value.selection.definition;
      delete value.selection.plan;
    }
    return invoke('adhocFlow:snapshotSelection', value);
  },
  adhocFlowDesignPreview: (invoke) => (payload) =>
    invoke('adhocFlow:designPreview', designPreviewPayload(payload)),
  adhocFlowExecutionPreview: (invoke) => (payload) => invoke('adhocFlow:executionPreview', payload || {}),
  adhocFlowPromote: (invoke) => (payload) => invoke('adhocFlow:promote', payload || {}),
  adhocFlowSaveSettings: (invoke) => (payload) => invoke('adhocFlow:saveSettings', payload || {}),
  designSessionList: (invoke) => (payload) => invoke('designSession:list', payload || {}),
  designSessionGet: (invoke) => (payload) => invoke('designSession:get', payload || {}),
  designSessionStart: (invoke) => (payload) => invoke('designSession:start', payload || {}),
  designSessionDelete: (invoke) => (payload) => invoke('designSession:delete', payload || {}),
  workflowTaskList: (invoke) => () => invoke('workflowTask:list', {}),
  workflowTaskCreate: (invoke) => (payload) => invoke('workflowTask:create', payload || {}),
  workflowTaskDelete: (invoke) => (payload) => invoke('workflowTask:delete', payload || {}),
  workflowTaskExecute: (invoke) => (payload) => invoke('workflowTask:execute', payload || {}),
  preparationRecommend: (invoke) => (payload) => invoke('preparation:recommend', payload || {}),
  preparationList: (invoke) => (payload) => invoke('preparation:list', payload || {}),
  preparationGet: (invoke) => (payload) => invoke('preparation:get', payload || {}),
  preparationCreate: (invoke) => (payload) => invoke('preparation:create', preparationPayload(payload)),
  preparationCreatePackage: (invoke) =>
    (payload) => invoke('preparation:createPackage', preparationPayload(payload)),
  preparationDelete: (invoke) => (payload) => invoke('preparation:delete', payload || {}),
  preparationStartDesign: (invoke) => (payload) => invoke('preparation:startDesign', {
    id: payload && payload.id,
  }),
  preparationSyncDesign: (invoke) => (payload) => invoke('preparation:syncDesign', payload || {}),
  preparationReviseDesign: (invoke) => (payload) => invoke('preparation:reviseDesign', payload || {}),
  preparationCompleteDesign: (invoke) => (payload) => invoke('preparation:completeDesign', payload || {}),
  preparationHandoff: (invoke) => (payload) => invoke('preparation:handoff', payload || {}),
};
