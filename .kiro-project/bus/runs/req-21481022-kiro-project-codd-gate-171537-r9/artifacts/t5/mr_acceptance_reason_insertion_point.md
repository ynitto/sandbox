# 検収判定（mr.py 相当）— done / 差し戻し理由テキストの組み立て箇所と挿入位置

対象: `tools/kiro-project/kiro-project.py`（**`kiro_project/mr.py` という別ファイルはリポジトリ内に
存在しない**。kiro-project は単一ファイル実装で、MR 関連処理・検収判定はすべて `kiro-project.py`
に同居している。以下はこのファイルの行番号で示す）。

## 前提の訂正

タスク文面は `kiro_project/mr.py` を指すが、`find`/`grep` で全リポジトリを走査しても該当ファイルは
無い（`tools/kiro-project/` 配下は `kiro-project.py` 1 本 + `codd_gate_*.py` 6 本の構成。t6 の
`kiro_project/model.py` 調査と同型の前提ズレ）。`mr.py` はモジュール分割後を想定した仮称と判断し、
実体である `tools/kiro-project/kiro-project.py` 内の **MR（merge request）関連関数群**
（`_task_mr_coords`/`ensure_task_mr`/`finalize_task_mr`/`close_task_mr`、L5142-5317）と、それらを
呼び出す検収判定の本体 `cmd_approve()`（L6808-6904）を調査対象として読んだ。

---

## 1. 検収判定の全体像

kiro-project のタスク終端状態には「差し戻し」と「却下」の2種類があり、コード冒頭のコメント
（L52）で明確に区別されている:

> 差し戻し（needs feedback）は kiro-project がタスクを修正して再提案、却下（reject）は廃止＋再計画。

タスク文面の「done / 差し戻し」はこのうち前者（却下ではなく、直せば再提出できる方）を指す。
差し戻し関連の分岐はコードベースに複数あるが、**「検収（verify=PASS 後の人の承認）」段階で
done か差し戻しかを決めているのは `cmd_approve()` の `review` 分岐（L6848-6891）ただ1箇所**である:

| 段階 | 状態遷移 | 関数 | 備考 |
|---|---|---|---|
| 実行前レビュー | `proposed` → `ready`/差し戻し | `_plan_approve`/`plan_rework`（L2218, L2243） | verify 実行**前**の計画レビュー。検収ではない |
| verify=PASS 後、ゲート対象なら | `doing` → `review` | `_settle_review`（L5366-5402） | 「検収待ち」の理由文（`gate_why`）を組み立てるが、**まだ done でも差し戻しでもない**（待機状態） |
| **検収（人が approve）** | `review` → `done` / `review`（差し戻し） | **`cmd_approve()` の `review` 分岐（L6848-6891）** | **本タスクが特定すべき箇所** |
| needs チェックボックス経由の代替経路 | `review` → `ready`（差し戻し・feedback あり）/ `ready`（承認・feedback なし） | `ingest_feedback()`（L2152-2191） | CLI `approve` を介さず needs/*.md の `[x]` で決着させる経路。MR クリーン判定を経ない簡易系（後述） |

`cmd_approve()` が「唯一の検収判定」である根拠:
- `finalize_task_mr()`（L5249, docstring L5250）が「approve（検収承認）時にタスク MR を…自動決着する」と明記し、`cmd_approve()` の `review` 分岐（L6851）からのみ呼ばれる。
- 既存テスト `test_approve_unclean_mr_keeps_review`（test_kiro_project.py:7818-7843）が、`cmd_approve()` を通した MR 未クリーン時の「差し戻し」（`rc == 1`、`review` のまま、通知本文に `"差し戻し"` を含む）を検収判定として直接検証しており、これがテストされている唯一の done/差し戻し決着点。

---

## 2. done / 差し戻しの理由テキストを組み立てている箇所

`cmd_approve()` の `review` 分岐は次の3ステップで構成される（L6848-6891）:

```python
    if t.norm_status() == "review":
        # 成果物レビューの承認: タスク MR があれば Stage 2 と同一規則で自動決着（クリーンなら
        # マージ・未クリーンなら差し戻しコメントを付けて review のまま）。MR 無しは従来どおり
        mr_ok, mr_msg = finalize_task_mr(cfg, t)                                   # L6851 ← 挿入位置
        if not mr_ok:
            write_needs_file(cfg, t, f"承認されたが MR が未クリーン: {mr_msg}", review=True,   # L6853: 差し戻し理由
                             evidence=f"- MR: {t.get('mr_url', '')}")
            print(f"{tid}: MR が未クリーンのため done にできません（{mr_msg}）。"
                  f"解消後に再度 approve してください。", file=sys.stderr)
            return 1
        if mr_msg:
            print(f"{tid}: {mr_msg}")
        # 検収ゲートの承認 = done 確定（verify は実行済み。保持した成果参照で納品書を書く）
        ex = dict(t.extra)
        ...
        vmsg = ex.get("gate_vmsg", "")
        t.status = "done"
        ...
        if cfg.do_archive:
            archive_task(cfg, t, vmsg or f"承認: {reason}", ref, ts, evidence=gate_ev)   # L6875: done 理由
        ...
        dr = append_decision(cfg, tid, cfg.actor, context=f"{tid}（{t.title}）を検収承認",
                             action="approve-done", reason=reason, affects=f"{tid} → done",
                             learn=(t.title, reason) if reason and cfg.learn_capture else None)
```

- **差し戻し理由**: `finalize_task_mr()`（L5249-5297）が GitLab MR のコンフリクト/未解決コメントを
  検査し `problems` リストを組み立て（L5267-5281）、`why = "; ".join(problems)`（L5283）として
  `(False, why)` を返す。それが `cmd_approve()` の `mr_msg` に入り、L6853 の
  `f"承認されたが MR が未クリーン: {mr_msg}"` として `write_needs_file`（review=True＝差し戻し）に渡る。
- **done 理由**: `vmsg`（`_settle_review` が保持した元の verify メッセージ）を優先し、無ければ
  人が CLI で渡した `reason` を `f"承認: {reason}"` として `archive_task`（L6875）と
  `append_decision`（L6880-6883）に渡る。

---

## 3. 挿入位置を1点に確定

**`kiro-project.py:6851`（`mr_ok, mr_msg = finalize_task_mr(cfg, t)` の直後、L6852 の
`if not mr_ok:` 分岐の直前）**。

ここで codd-gate verify のヘルパ（t9 で新設予定）を呼び、結果を既存の `(mr_ok, mr_msg)` へ
マージする（例: `mr_ok = mr_ok and codd_ok`／`mr_msg = "; ".join(filter(None, [mr_msg, codd_msg]))`）
だけで、以下の**両方**に自動的に波及する:

- **差し戻し側**: `mr_msg` は L6853 の f-string にそのまま使われているため、codd-gate のドリフト内容が
  差し戻し理由に含まれる（t14 の要求「FAIL なら差し戻し理由に具体的なドリフト項目を列挙する」を満たす）。
  `mr_ok = False` にすれば L6852 の分岐へ入り、`review` のまま留まって done を確定させない。
- **done 側**: codd-gate が PASS の場合はこの分岐に入らず、そのまま L6860 以降の done 確定処理へ
  進む。done 側の理由文（`vmsg or f"承認: {reason}"`）自体は変更不要（t14 の要求は FAIL 側のみ
  ドリフト列挙を求めており、PASS 側は「阻害しない」ことが条件）。

### この1点を選んだ理由（他候補を退けた根拠）

- **`finalize_task_mr()` 内部（L5249 の関数内）ではなく呼び出し側（L6851）にする**: `finalize_task_mr`
  は `_task_mr_coords(task)` が `None`（GitLab MR が無いタスク）だと L5255-5256 で即
  `(True, "")` を返して早期リターンする。GitLab executor を使わないタスクは珍しくなく、
  codd-gate チェックを関数内部に埋めるとその早期リターンで一緒にスキップされてしまう。
  codd-gate はリポジトリの差分ドリフトを見るものでGitLab MR の有無とは無関係に実行すべきなので、
  呼び出し側でマージする方が正しい。
- **`_settle_review()`（L5366-5402、検収待ちへの遷移時）ではない**: ここはまだ人の承認前の
  「検収待ち」状態を作るだけで、done/差し戻しのどちらにも確定しない。タスク文面が指す
  「done / 差し戻しの理由テキスト」の組み立てには早すぎる。
- **`ingest_feedback()`（L2152-2191、needs チェックボックス経由の代替決着経路）は対象外**:
  こちらは `finalize_task_mr` を経由しない簡易系（MR クリーン判定なし）で、`cmd_approve()` とは
  独立した別経路。t20（mr.py フックの統合テスト）が `cmd_approve` を対象にしていることからも、
  結線対象は `cmd_approve()` 側で確定してよいと判断した。

### 次工程（t13/t14 実装）への申し送り

- 挿入点では `cfg: Config` と `t: Task` の両方がスコープ内にあり、`_task_verify_cwd(cfg, t)`
  （L3098-3133）で codd-gate 実行時の cwd（clone ルート）を取得できる。これは既存の
  `codd_gate_routing.build_routing_args()` が要求する `vcwd` 引数とそのまま対応する
  （同モジュールの docstring が明記する想定呼び出し元の1つ）。
- t9 のヘルパが `(ok: bool, msg: str)` 形式（`finalize_task_mr` と同じ形）を返せば、
  上記のマージ処理はこの1点への数行差し込みで完結する。

---

## 検証内容と結果

- `find`/`grep` によるファイル探索: `tools/kiro-project/` 配下に `mr.py` は存在しないことを確認。
- `grep -n` で `検収`/`差し戻し`/`finalize_task_mr`/`mr_ok`/`mr_msg` の全出現箇所を洗い出し、
  該当箇所を `Read` で全文確認（L1991-2280, L2347-2413, L5142-5422, L6790-6934, L7074-7120）。
- 既存テスト `test_approve_unclean_mr_keeps_review`（tests/test_kiro_project.py:7818-7843）を読み、
  `cmd_approve()` の `review` 分岐が実際に done/差し戻しを決着させるテスト対象であることを裏取りした。
- 完了条件コマンドのうち実行可能な部分を試行: `python3 -m pytest tools/kiro-project/tests -q -k codd`
  → **50 passed**。`grep -rq "codd_gate" tools/kiro-project/kiro_project/` は該当ディレクトリが
  存在せず失敗（t3 と同じ既知の未達要因。後続の結線タスクの責務）。
- 本タスクは調査のみのため作業ツリーへの変更なし。

## 前提・未解決事項・範囲外で見つけた問題

- 前提: `kiro_project/mr.py` は実体不在のため `tools/kiro-project/kiro-project.py` の
  `cmd_approve()`/`finalize_task_mr()` を対象とした（上記「前提の訂正」参照）。t6 の
  `kiro_project/model.py`（実体は同じく `kiro-project.py`）と同型の前提ズレであり、run 全体で
  一貫した解釈と判断した。
- 未解決事項: codd-gate FAIL 時に `mr_ok = False` へ倒すと L6852 の `return 1` に入り
  `cmd_approve` 呼び出し元（CLI）へエラー終了が返る。これは既存の MR 未クリーン時と同じ扱いで
  一貫しているが、「codd-gate 由来の差し戻し」と「GitLab MR 由来の差し戻し」を通知文面上で
  区別すべきかは未確定（t14 の設計判断に委ねる）。
- 範囲外で見つけた問題（このタスクでは修正しない）: `ingest_feedback()`（L2152-2191）は
  `finalize_task_mr` を経由しない別の検収決着経路であり、`cmd_approve` 側にのみ codd-gate を
  結線すると、needs チェックボックス経由で承認されたタスクには codd-gate 結果が反映されない
  非対称が残る。実運用上どちらが主経路かは本タスクの調査範囲外のため、t8/t13 での方針確認が必要。
