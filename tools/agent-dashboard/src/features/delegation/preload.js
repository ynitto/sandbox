'use strict';

// 委譲制御面の renderer API。payload は封筒の部分形（op/version/id はメインが補う）。
// - post:   { workload, goal, title?, design?, workspace?, references?, policy?, acceptance?,
//             budget?, deadline?, priority?, engine?, home?（amigos）, busDir?（flow） }
// - award:  { workload:'amigos', id, unit, node, home }
// - accept: { workload:'amigos', id, home }
// - reject: { workload:'amigos', id, feedback, home }
// - cancel: { workload, id, reason?, home?（amigos）, busDir?（flow） }
// - nodeCommand: { action:'board-bid'|'board-cancel'|'board-award', id, boardRepo?, node?, reason? }
//   板へ直接書かず、この PC の常駐体へ指示を投函する（S8-2/8-3）。
module.exports = {
  delegationList: (invoke) => (payload) => invoke('delegation:list', payload || {}),
  delegationNodes: (invoke) => () => invoke('delegation:nodes', {}),
  delegationNodeCommand: (invoke) => (payload) => invoke('delegation:nodeCommand', payload || {}),
  delegationPost: (invoke) => (payload) => invoke('delegation:post', payload || {}),
  delegationAward: (invoke) => (payload) => invoke('delegation:award', payload || {}),
  delegationAccept: (invoke) => (payload) => invoke('delegation:accept', payload || {}),
  delegationReject: (invoke) => (payload) => invoke('delegation:reject', payload || {}),
  delegationCancel: (invoke) => (payload) => invoke('delegation:cancel', payload || {}),
};
