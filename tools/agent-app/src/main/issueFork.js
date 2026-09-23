'use strict';

const path = require('path');

function targetRepo({ config, issue, evidence = [], sourceSession = null, catalog }) {
  const repos = config.repos || [];
  const origins = [...new Set(evidence.filter((item) => item.artifact?.kind === issue.target.kind
    && item.artifact.name === issue.target.name && item.artifact.origin.startsWith('repo:'))
    .map((item) => item.artifact.origin.slice(5)))];
  let repo = '';
  if (origins.length === 1) {
    const matches = repos.filter((candidate) => path.basename(candidate) === origins[0]);
    if (matches.length === 1) repo = matches[0];
  }
  if (!repo && sourceSession && repos.includes(sourceSession.repo)) repo = sourceSession.repo;
  if (issue.target.kind === 'skill') {
    const found = repo ? catalog(repo).find((item) => item.name === issue.target.name) : null;
    if (!found || found.place === 'home') {
      repo = config.audit.skillRepositoryPath;
      if (!repo) throw new Error('設定でスキル管理リポジトリのローカルパスを指定してください');
    }
  }
  if (!repo || !repos.includes(repo)) throw new Error('修正先のリポジトリを特定できません');
  return repo;
}

module.exports = { targetRepo };
