'use strict';

// GitLab REST API v4 の軽量クライアント（gitlab-review-viewer の流儀）。
// このアプリでは読み取り専用: kiro-flow の gitlab executor が起票した
// イシューの「今」の状態（state/labels/関連 MR）を補完表示するのに使う。
// 書き込み系のレビュー操作は gitlab-review-viewer へ引き継ぐ。

let netFetch = typeof fetch === 'function' ? fetch : null;
try {
  const { net } = require('electron');
  if (net && net.fetch) netFetch = net.fetch.bind(net);
} catch {
  /* Electron 外（テスト時）は global fetch */
}

class GitLabError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

class GitLabClient {
  constructor({ baseUrl, token }) {
    this.baseUrl = String(baseUrl || '').replace(/\/+$/, '');
    this.token = token || '';
  }

  get enabled() {
    return Boolean(this.baseUrl && this.token);
  }

  async api(pathname, { query } = {}) {
    const url = new URL(`${this.baseUrl}/api/v4${pathname}`);
    for (const [k, v] of Object.entries(query || {})) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v));
    }
    const res = await netFetch(url.toString(), {
      headers: { 'PRIVATE-TOKEN': this.token },
    });
    const text = await res.text();
    let body = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = text;
    }
    if (!res.ok) {
      const msg = body && body.message ? JSON.stringify(body.message) : `HTTP ${res.status}`;
      throw new GitLabError(`GitLab API エラー: ${msg}`, res.status, body);
    }
    return body;
  }

  // イシューの web_url（https://host/group/proj/-/issues/123）を
  // {projectPath, type, iid} に分解する。MR の URL も受ける。
  static parseUrl(webUrl) {
    try {
      const u = new URL(String(webUrl));
      const m = u.pathname.match(/^\/(.+?)\/-\/(issues|merge_requests)\/(\d+)/);
      if (!m) return null;
      return {
        host: u.origin,
        projectPath: m[1],
        type: m[2] === 'issues' ? 'issue' : 'mr',
        iid: parseInt(m[3], 10),
      };
    } catch {
      return null;
    }
  }

  async getIssueByUrl(webUrl) {
    const parsed = GitLabClient.parseUrl(webUrl);
    if (!parsed || parsed.type !== 'issue') throw new Error(`イシュー URL を解釈できません: ${webUrl}`);
    const enc = encodeURIComponent(parsed.projectPath);
    const issue = await this.api(`/projects/${enc}/issues/${parsed.iid}`);
    let relatedMrs = [];
    try {
      relatedMrs = await this.api(`/projects/${enc}/issues/${parsed.iid}/related_merge_requests`);
    } catch {
      relatedMrs = [];
    }
    return {
      projectPath: parsed.projectPath,
      projectId: issue.project_id,
      iid: issue.iid,
      title: issue.title,
      state: issue.state,
      labels: issue.labels || [],
      url: issue.web_url,
      updatedAt: issue.updated_at,
      author: issue.author ? issue.author.username : '',
      relatedMrs: (relatedMrs || []).map((mr) => ({
        iid: mr.iid,
        title: mr.title,
        state: mr.state,
        mergedAt: mr.merged_at || null,
        url: mr.web_url,
      })),
    };
  }

  // プロジェクト（"group/proj" パス）のイシュー一覧（gitlab executor の
  // 規約ラベル status:* での絞り込みに対応）
  async listProjectIssues({ projectPath, state, labels, perPage = 50 }) {
    const enc = encodeURIComponent(projectPath);
    const issues = await this.api(`/projects/${enc}/issues`, {
      query: {
        state: state || undefined,
        labels: labels || undefined,
        per_page: perPage,
        order_by: 'updated_at',
      },
    });
    return (issues || []).map((it) => ({
      projectPath,
      projectId: it.project_id,
      iid: it.iid,
      title: it.title,
      state: it.state,
      labels: it.labels || [],
      url: it.web_url,
      updatedAt: it.updated_at,
      author: it.author ? it.author.username : '',
    }));
  }
}

module.exports = { GitLabClient, GitLabError };
