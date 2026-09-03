'use strict';

module.exports = {
  documentsOverview: (invoke) => (payload) => invoke('documents:overview', payload || {}),
  documentsGet: (invoke) => (payload) => invoke('documents:get', payload || {}),
  documentsCreate: (invoke) => (payload) => invoke('documents:create', payload || {}),
  documentsResume: (invoke) => (payload) => invoke('documents:resume', payload || {}),
  documentsVerify: (invoke) => (payload) => invoke('documents:verify', payload || {}),
  documentsFeedback: (invoke) => (payload) => invoke('documents:feedback', payload || {}),
  documentsRuleFromHistory: (invoke) => (payload) => invoke('documents:ruleFromHistory', payload || {}),
  documentsRuleRead: (invoke) => (payload) => invoke('documents:ruleRead', payload || {}),
  documentsRuleDraft: (invoke) => (payload) => invoke('documents:ruleDraft', payload || {}),
  documentsRuleSave: (invoke) => (payload) => invoke('documents:ruleSave', payload || {}),
  documentsPickInputs: (invoke) => () => invoke('documents:pickInputs', {}),
  documentsPickFolder: (invoke) => (payload) => invoke('documents:pickFolder', payload || {}),
  documentsSaveSettings: (invoke) => (payload) => invoke('documents:saveSettings', payload || {}),
};
