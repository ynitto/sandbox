interface TextlintMessage {
  ruleId: string;
  message: string;
  range: [number, number];
  line: number;
  column: number;
  severity: number;
}

export interface TextlintResult {
  filePath: string;
  messages: TextlintMessage[];
}

type TextlintrcConfig = Record<string, unknown>;

export class TextlintWorker {
  private worker: Worker | null = null;
  private configSent = false;
  private currentTextlintrc: TextlintrcConfig = {};
  private readonly workerUrl: string;

  constructor(workerUrl: string) {
    this.workerUrl = workerUrl;
  }

  private ensureWorker(): Worker {
    if (!this.worker) {
      this.worker = new Worker(this.workerUrl);
    }
    return this.worker;
  }

  setTextlintrc(config: TextlintrcConfig): void {
    this.currentTextlintrc = config;
    this.configSent = false;
    if (this.worker) {
      this.worker.postMessage({ command: 'merge-config', args: { textlintrc: config } });
      this.configSent = true;
    }
  }

  lint(text: string): Promise<TextlintResult> {
    return new Promise((resolve, reject) => {
      const worker = this.ensureWorker();

      if (!this.configSent) {
        worker.postMessage({ command: 'merge-config', args: { textlintrc: this.currentTextlintrc } });
        this.configSent = true;
      }

      const onMessage = (e: MessageEvent) => {
        if (e.data.command === 'lint:result') {
          worker.removeEventListener('message', onMessage);
          worker.removeEventListener('error', onError);
          resolve(e.data.args.result as TextlintResult);
        }
      };

      const onError = (e: ErrorEvent) => {
        worker.removeEventListener('message', onMessage);
        worker.removeEventListener('error', onError);
        reject(new Error(e.message));
      };

      worker.addEventListener('message', onMessage);
      worker.addEventListener('error', onError);
      worker.postMessage({ command: 'lint', args: { text } });
    });
  }

  terminate(): void {
    this.worker?.terminate();
    this.worker = null;
    this.configSent = false;
  }
}
