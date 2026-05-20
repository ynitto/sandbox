import { App, Modal, Setting } from "obsidian";

export class TextInputModal extends Modal {
	private value: string;

	constructor(
		app: App,
		private opts: {
			title: string;
			description?: string;
			placeholder?: string;
			defaultValue?: string;
			multiline?: boolean;
			submitText?: string;
			onSubmit: (value: string) => void;
		}
	) {
		super(app);
		this.value = opts.defaultValue ?? "";
	}

	onOpen(): void {
		const { contentEl } = this;
		contentEl.createEl("h3", { text: this.opts.title });
		if (this.opts.description) {
			contentEl.createEl("p", { text: this.opts.description, cls: "setting-item-description" });
		}

		const inputWrap = contentEl.createDiv();
		inputWrap.style.margin = "8px 0";

		let inputEl: HTMLInputElement | HTMLTextAreaElement;
		if (this.opts.multiline) {
			inputEl = inputWrap.createEl("textarea");
			inputEl.rows = 6;
		} else {
			inputEl = inputWrap.createEl("input", { type: "text" });
		}
		inputEl.style.width = "100%";
		inputEl.value = this.value;
		if (this.opts.placeholder) inputEl.placeholder = this.opts.placeholder;
		inputEl.addEventListener("input", () => {
			this.value = inputEl.value;
		});
		inputEl.focus();

		if (!this.opts.multiline) {
			inputEl.addEventListener("keydown", (e: KeyboardEvent) => {
				if (e.key === "Enter") {
					e.preventDefault();
					this.submit();
				}
			});
		}

		new Setting(contentEl)
			.addButton((btn) =>
				btn
					.setButtonText(this.opts.submitText ?? "Submit")
					.setCta()
					.onClick(() => this.submit())
			)
			.addButton((btn) => btn.setButtonText("Cancel").onClick(() => this.close()));
	}

	private submit(): void {
		const v = this.value.trim();
		if (!v) return;
		this.close();
		this.opts.onSubmit(v);
	}

	onClose(): void {
		this.contentEl.empty();
	}
}

export class LabelMultiSelectModal extends Modal {
	private selected = new Set<string>();

	constructor(
		app: App,
		private opts: {
			title: string;
			description?: string;
			labels: string[];
			submitText?: string;
			onSubmit: (selected: string[]) => void;
		}
	) {
		super(app);
	}

	onOpen(): void {
		const { contentEl } = this;
		contentEl.createEl("h3", { text: this.opts.title });
		if (this.opts.description) {
			contentEl.createEl("p", { text: this.opts.description, cls: "setting-item-description" });
		}

		if (this.opts.labels.length === 0) {
			contentEl.createEl("p", { text: "No labels on this issue." });
			new Setting(contentEl).addButton((btn) =>
				btn.setButtonText("Close").onClick(() => this.close())
			);
			return;
		}

		const listEl = contentEl.createDiv();
		listEl.style.margin = "8px 0";

		this.opts.labels.forEach((label) => {
			const row = listEl.createDiv();
			row.style.display = "flex";
			row.style.alignItems = "center";
			row.style.gap = "6px";
			row.style.padding = "2px 0";

			const cb = row.createEl("input", { type: "checkbox" });
			cb.id = `lbl-${label}`;
			cb.addEventListener("change", () => {
				if (cb.checked) this.selected.add(label);
				else this.selected.delete(label);
			});
			const lblEl = row.createEl("label", { text: label });
			lblEl.htmlFor = cb.id;
		});

		new Setting(contentEl)
			.addButton((btn) =>
				btn
					.setButtonText(this.opts.submitText ?? "Submit")
					.setCta()
					.onClick(() => {
						if (this.selected.size === 0) return;
						this.close();
						this.opts.onSubmit(Array.from(this.selected));
					})
			)
			.addButton((btn) => btn.setButtonText("Cancel").onClick(() => this.close()));
	}

	onClose(): void {
		this.contentEl.empty();
	}
}

export class ConfirmModal extends Modal {
	constructor(
		app: App,
		private opts: {
			title: string;
			message: string;
			submitText?: string;
			onConfirm: () => void;
		}
	) {
		super(app);
	}

	onOpen(): void {
		const { contentEl } = this;
		contentEl.createEl("h3", { text: this.opts.title });
		contentEl.createEl("p", { text: this.opts.message });

		new Setting(contentEl)
			.addButton((btn) =>
				btn
					.setButtonText(this.opts.submitText ?? "OK")
					.setCta()
					.onClick(() => {
						this.close();
						this.opts.onConfirm();
					})
			)
			.addButton((btn) => btn.setButtonText("Cancel").onClick(() => this.close()));
	}

	onClose(): void {
		this.contentEl.empty();
	}
}
