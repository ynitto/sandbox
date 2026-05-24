import { ItemView, WorkspaceLeaf, TFile } from "obsidian";
import { GitlabIssuesSettings } from "../SettingsTab/settings-types";
import { getActiveIssueRef } from "./actions";
import { IssueActionsForm, IssueActionsFormHooks } from "./form";

export const ISSUE_ACTIONS_VIEW_TYPE = "gitlab-issue-actions-view";

export class IssueActionsView extends ItemView {
	private form: IssueActionsForm;
	private trackedFilePath: string | null = null;
	private hasRenderedOnce = false;

	constructor(
		leaf: WorkspaceLeaf,
		settings: GitlabIssuesSettings,
		hooks: IssueActionsFormHooks
	) {
		super(leaf);
		this.form = new IssueActionsForm(this.app, settings, hooks);
	}

	getViewType(): string {
		return ISSUE_ACTIONS_VIEW_TYPE;
	}

	getDisplayText(): string {
		return "Gitlab Issue";
	}

	getIcon(): string {
		return "git-pull-request";
	}

	async onOpen(): Promise<void> {
		this.refresh();
		this.registerEvent(
			this.app.workspace.on("active-leaf-change", () => this.refresh())
		);
		this.registerEvent(
			this.app.metadataCache.on("changed", (file: TFile) => {
				if (this.trackedFilePath && file.path === this.trackedFilePath) {
					this.refresh();
				}
			})
		);
	}

	async onClose(): Promise<void> {
		this.contentEl.empty();
	}

	private refresh(): void {
		const ctx = getActiveIssueRef(this.app);

		if (!ctx) {
			// Active note isn't an issue. Keep the existing form intact so the
			// user can read related MRs / other notes while composing a comment.
			// Only render the empty-state placeholder once, before any issue
			// has ever been loaded into the panel.
			if (!this.hasRenderedOnce) {
				this.form.render(this.contentEl, null);
				this.hasRenderedOnce = true;
			}
			return;
		}

		if (ctx.file.path === this.trackedFilePath) {
			// Same issue — preserve in-progress comment text and input values.
			return;
		}

		this.trackedFilePath = ctx.file.path;
		this.form.render(this.contentEl, ctx);
		this.hasRenderedOnce = true;
	}
}
