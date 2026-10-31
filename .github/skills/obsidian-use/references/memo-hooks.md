# メモを集めるフック（OBSIDIAN NOTE）

## 目次

- [何をするか](#何をするか)
- [moltbook-use のフックとの違い](#moltbook-use-のフックとの違い)
- [OBSIDIAN NOTE が出たら](#obsidian-note-が出たら)
- [入れる・点検する・外す](#入れる点検する外す)
- [調整](#調整)

---

## 何をするか

セッションのフック（`scripts/obsidian_hook.py`）が、作業中の出来事を控え、出来事があったときだけ
ターンの終わりにエージェントへ振り返りの 1 ターンを足させる。エージェントは役立つことだけを
`vault.py memo` で 1 件ずつ Obsidian の wiki に残す。LLM を別に呼ばず、会話のログも読まない。

| きっかけ | 控えるもの |
|---|---|
| ツールの失敗 | ツール名とエラーの 1 行 |
| 失敗からの回復 | 同じツールがあとで成功した |
| 言い直し | 「違う」「ではなく」「やり直して」など |
| メモの依頼 | 「メモして」「覚えておいて」「Obsidian に」など |
| まとまった作業 | ツールを一定回数（既定 15 回）使った（1 セッション 1 回） |

| フック | 動き |
|---|---|
| 開始 | このプロジェクト・この環境の既存のメモの題を数行だけ会話に足す（ボルトのフォルダを読むだけ。Obsidian は呼ばない） |
| プロンプト | 言い直し・メモの依頼を控える。kiro-cli v2 は前のターンの振り返りの指示をここで前置きする |
| ツール | 失敗・回復・回数を控える（`vault.py` 自身の実行は数えない） |
| 終わり | 出来事があれば `OBSIDIAN NOTE` の指示を返す（Copilot・Claude Code・kiro v3 は block。振り返りのターンの終わりには出さない） |

上限: 指示は 1 セッション 3 回、メモは 5 件まで。控えは 1 セッション 20 件まで、7 日で消える。

## moltbook-use のフックとの違い

| | moltbook-use（MOLTBOOK RETRO） | obsidian-use（OBSIDIAN NOTE） |
|---|---|---|
| 書き先 | 共有の GitLab（他の人が読む） | 自分の Obsidian ボルト |
| 書いてよいこと | どこでも通じる原因と回避だけ。パス・リポジトリ名・社内の名前は書かない | プロジェクト・環境に固有のことも書く（ビルド手順、置き場所、ブランチ、ホストの癖） |
| 書いてはいけないこと | 秘密情報・個人を指す文 | 秘密情報（トークン・鍵・パスワード。`vault.py` が見つけたら書かない） |
| 重複 | 名前・エラーの署名・キーワードで +1 | 同じフォルダで同じ `--name` なら「起きた回」を足す |

両方を同じエージェントに入れてよい（互いの印には触らない）。両方の指示が出たら、他の人の役に立つ一般的な
ことは Moltbook に、このプロジェクト・この PC だけのことは Obsidian に書き分ける。同じことを両方に書かない。

## OBSIDIAN NOTE が出たら

利用者に訊かずに、次に同じ作業をするときに役立つことだけを書く。無ければ何もしない。

```bash
python scripts/vault.py memo --session <指示にある番号> --scope project --kind fact \
  --name e2e-needs-xvfb --title "E2E は xvfb-run で回す" \
  --detail "xvfb-run -a npm test（画面の無い環境では Electron が起動しない）" \
  --keywords "e2e,xvfb,electron"
```

| `--scope` | 書くこと |
|---|---|
| `project` | このリポジトリだけで通じること（ビルド・テストの手順、置き場所、決めたこと、詰まりと回避） |
| `env` | この PC・OS・ツールの版だけで起きること |
| `knowledge` | どこでも通じる知識（記事や資料の整理は ingest で） |

| `--kind` | `--reason` の意味 | `--detail` の意味 |
|---|---|---|
| `bug` | 原因（根拠つき） | 回避 |
| `tip` | 理由 | やること |
| `fact` | 根拠 | 内容 |
| `decision` | 理由 | 決めたこと |

- 指示の「既にあるメモ」に同じことがあれば、その `--name` を使う（追記になる）。
- 出力は 1 行（`OK <パス> に書きました` / `OK <パス> に追記しました` / `NG 直すべき引数`）。
- 利用者への返事は「Obsidian: <書いたパス>」の 1 行だけ。

## 入れる・点検する・外す

| 環境 | 入れる | 点検 / 外す |
|---|---|---|
| Windows（Copilot アプリ ＋ WSL の kiro-cli） | `powershell -ExecutionPolicy Bypass -File scripts/hook_install.ps1 -Vault "<ボルト>"` | `-Doctor` / `-Uninstall` |
| WSL・Linux の kiro-cli | `sh scripts/hook_install.sh --vault <ボルト>` | `doctor` / `uninstall` |
| Claude Code | `sh scripts/hook_install.sh install --client claude --vault <ボルト>` | `doctor --client claude` / `uninstall --client claude` |

- インストーラはスキルを `<エージェントの置き場所>/skills/obsidian-use` に写し、ボルトの骨組みと設定を作り、フックを足す。
- Copilot アプリはプラグインのマーケットプレイスを足す操作だけ利用者が行う（インストーラが場所を出す）。
- kiro-cli v3 のエンジンなら `--v3`（ps1 は `-V3`）でグローバルフックも置く。
- 外しても、ボルトのメモ・控え・スキルの写しは残る。

## 調整

- 語・上限: [`memo_rules.json`](memo_rules.json)（`correction` / `remember` / `work_tools` / `max_notes` など）
- 止めたいリポジトリ: 直下に `.obsidian-memo-ignore` を置くか、`exclude_cwd` にフォルダを足す
- 一時的に止める: 環境変数 `OBSIDIAN_MEMO_OFF=1`
