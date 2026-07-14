'use strict';

// GitLab REST API v4 の軽量クライアント（gitlab-review-viewer の流儀）。
// このアプリでは読み取り専用: agent-flow の gitlab executor が起票した
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

  // イシューの人コメント（notes）を古い順に取得する。gitlab executor の外部クローズ判定
  // （_decision_from_comments）と同じく、承認/却下の手掛かり語をビュアー側で読むのに使う。
  async getIssueComments(projectPath, iid) {
    const enc = encodeURIComponent(projectPath);
    return this.api(`/projects/${enc}/issues/${iid}/notes`, {
      query: { per_page: 100, order_by: 'created_at', sort: 'asc' },
    });
  }

  // リポジトリ URL（https://host/group/proj(.git)）→ "group/proj" パス。
  // agent-flow の run meta にはワークスペースのリポジトリ URL しか無いため、
  // イシュー検索はこれで起票先プロジェクトを解決する（gitlab executor と同じ考え方）。
  static repoUrlToProjectPath(repoUrl) {
    try {
      const u = new URL(String(repoUrl));
      const p = u.pathname.replace(/\.git$/, '').replace(/^\/+|\/+$/g, '');
      return p || null;
    } catch {
      return null;
    }
  }

  // agent-flow gitlab executor の決定的タスクトークン（イシュー本文の隠しマーカー
  // `<!-- agent-flow:task-token:kf-... -->`）で関連イシューを探す。実行中（result 未確定）の
  // ノードでもイシューへたどり着ける。検索ヒットは本文のマーカー一致で必ず検証する
  // （executor の _find_open_issue_by_token と同じ流儀。state は絞らない＝却下済みも見つかる）。
  async findIssueByToken({ repoUrl, projectPath, token }) {
    const pp = projectPath || GitLabClient.repoUrlToProjectPath(repoUrl);
    if (!pp) throw new Error(`起票先プロジェクトを解決できません: ${repoUrl || '(URL なし)'}`);
    const enc = encodeURIComponent(pp);
    const marker = `agent-flow:task-token:${token}`;
    const issues = await this.api(`/projects/${enc}/issues`, {
      query: { search: token, in: 'description', per_page: 20, order_by: 'updated_at' },
    });
    for (const it of issues || []) {
      if (String(it.description || '').includes(marker)) {
        return this.getIssueByUrl(it.web_url); // 関連 MR 込みの完全な形で返す
      }
    }
    return null;
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
    return (issues || []).map((it) => {
      // agent-flow の gitlab executor が起票したイシューは本文に隠しマーカー
      // `<!-- agent-flow:task-token:kf-... -->` を持つ。トークンを取り出しておくと、
      // フロー run の各ノードが持つ決定的タスクトークン（nodeTaskToken）と突き合わせて
      // 「このレビュー待ちイシューはどの run のどのノードか」を追加コストなしで解決できる
      // （イシュー URL は承認/却下で result が確定するまで bus に現れないため、
      //  レビュー待ち中の対応付けはこのトークン一致が唯一確実な手がかりになる）。
      const tokenMatch = String(it.description || '').match(/agent-flow:task-token:(kf-[0-9a-f]+)/);
      return {
        projectPath,
        projectId: it.project_id,
        iid: it.iid,
        title: it.title,
        state: it.state,
        labels: it.labels || [],
        url: it.web_url,
        updatedAt: it.updated_at,
        author: it.author ? it.author.username : '',
        kiroFlow: Boolean(tokenMatch),
        taskToken: tokenMatch ? tokenMatch[1] : null,
      };
    });
  }
}

module.exports = { GitLabClient, GitLabError };
