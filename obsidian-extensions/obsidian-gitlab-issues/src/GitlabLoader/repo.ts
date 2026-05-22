import { References } from "./issue-types";

export function extractRepoPath(
	item: { references?: string | References; web_url?: string; project_id?: number },
	kind: "issues" | "merge_requests"
): string {
	if (item.references && typeof item.references === "object" && item.references.full) {
		return item.references.full.replace(/[#!]\d+$/, "");
	}
	if (item.web_url) {
		const re = new RegExp(`^https?://[^/]+/(.+?)/-/${kind}/\\d+`);
		const m = item.web_url.match(re);
		if (m) return m[1];
	}
	return item.project_id ? String(item.project_id) : "unknown";
}

export function sanitizeFolderSegment(value: string): string {
	return value.replace(/[*"\\<>|?:]/g, "-");
}

export function sanitizeRepoPath(repoPath: string): string {
	return repoPath
		.split("/")
		.map(sanitizeFolderSegment)
		.filter((s) => s.length > 0)
		.join("/");
}
