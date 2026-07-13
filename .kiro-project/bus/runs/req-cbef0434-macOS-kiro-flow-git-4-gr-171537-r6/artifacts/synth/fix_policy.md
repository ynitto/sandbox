# 修正方針の一本化（synth）

## 0. 前提の矛盾の指摘（鵜呑みにしなかった点）

本タスクは「gate1 を通過した原因に対する修正方針を一本化する」ことを依頼しているが、
`artifacts/gate1/verify_t6_t9_adversarial.md` の実際の判定は **`verify=fail`** であり、
t6〜t9 が挙げた4仮説（symlink 差／APFS 大小文字非区別／git config 差分／プラットフォーム前提4種）
はいずれも「通過」していない。理由は、対応付けるべき「macOS で失敗する git 自己修復テスト4件」
そのものの一次証拠（失敗 nodeid・トレースバック）が run 内に一件も存在しないため。

さらに、割当ワークスペースの `decisions/macOS-kiro-flow-git-4-gr-171537.md` に記録された
**DR-0010（2026-07-13、actor: nitto、= 本タスクの発注者本人の人間判断）**が、この矛盾の答えを
既に出している。

> DR-0010: 完了条件は満たしている（両スイート 990 passed）。120 秒の verify タイムアウトで
> NG 扱いされていたため、verify_timeout=600 に引き上げて再検証する。

したがって「テスト側の前提を直すか本体をプラットフォーム非依存化するか」という二択の前提自体が
事実と矛盾している。実在するのはどちらでもなく、**kiro-project 自身の verify ハーネスの
タイムアウト設定不足による偽陰性（false NG）**である。この結論は以下3系統の独立証拠で一致する。

1. **gate1（敵対的検証）**: 4件失敗の一次証拠なし。t6/t7/t8/t9 が調べた実装（`GitBus._is_own_repo_root`
   `_origin_matches`、`StateGitBus._is_managed`、`daemon_lock_key` 等）はいずれも realpath 正規化・
   環境変数明示注入・ブランチ非ハードコードで既に防御済み。
2. **完了条件コマンドの独立実測（本 run だけで4回以上）**: t1（900 passed）／t6 122.20s／
   t7（同条件）／t8 123.19s／gate1 121.30s。いずれも `exit 0`・失敗0件。実行時間が一貫して
   旧タイムアウト 120s をわずかに超えている。
3. **人間の判断（DR-0010）**: プロジェクト所有者が同じ結論（テストは green、原因は verify
   タイムアウト）に到達し、修正方向（600 への引き上げ）を明示済み。

なお t9 は「t3 成果物が空ディレクトリで欠落していた」ことを報告しており、依存タスク間の
成果物受け渡しに欠落があった旨も申し送る（本タスクの結論には影響しないが、run の運用上の
問題として記録する）。また DR-0010 の「990 passed」と t1/t6/t8/gate1 の「900 passed」は
件数が一致しない（測定タイミング差によるテスト数増減の可能性）。原因不明の軽微な不一致として
記録するが、いずれも「0 failed・exit 0」という結論には影響しない。

## 1. 統合判断（結論）

- **テスト側の前提修正: 不要。本体のプラットフォーム非依存化: 不要。**
  git 自己修復ロジック（`GitBus`/`StateGitBus` 等）にもテストコード
  （`test_kiro_flow.py`／`test_kiro_project.py`）にも、macOS 固有で再現する欠陥は見つからなかった
  （t6/t7/t8/t9 が独立に確認、gate1 もこれを覆す証拠なしと判定）。この2系統には**変更を加えない**。
- **修正対象はただ一つ: `tools/kiro-project/kiro-project.py` の `verify_timeout` 既定値。**
  これは git 自己修復ロジックのプラットフォーム対応ではなく、pytest スイートの実行時間が
  静的タイムアウト（120s）に対して不足しているという、kiro-project 自身の運用上の欠陥である。
- 上記いずれの選択も、症状（「NG と誤判定される」）を隠蔽するのではなく、原因
  （タイムアウト設定値そのものが実測実行時間を下回っている）を直接是正するものである。

## 2. 具体的な変更設計

対象リポジトリ: `https://github.com/ynitto/sandbox`（作業ブランチ `kp/macOS-kiro-flow-git-4-gr-171537`）
対象ファイル: `tools/kiro-project/kiro-project.py`

### 変更1: `Config` dataclass の既定値（L3959）

```python
# 変更前
verify_timeout: float = 120.0
# 変更後
verify_timeout: float = 600.0
```

### 変更2: CLI/設定ファイルの既定値辞書 `CONFIG_DEFAULTS`（L9513）

```python
# 変更前
"verify_timeout": 120.0,
# 変更後
"verify_timeout": 600.0,
```

**両方を揃えて直す必要がある理由**: `--verify-timeout` の argparse 既定値は `None`（L9838）で、
実際の既定値解決は `CONFIG_DEFAULTS["verify_timeout"]`（L9513）→ 未指定なら `Config.verify_timeout`
（L3959）の二重管理になっている。片方だけ変更すると、CLI 経由と設定ファイル未指定時とで
既定タイムアウトが食い違う不整合を新たに生む。

### 副次的な影響（バグではなく、意図した波及）

`missing_after()`（L4391 付近）は `cfg.act_timeout + cfg.verify_timeout + 60.0` を
「失踪検知」の猶予として使っている。`verify_timeout` を 600 に上げると、この派生値も
1980.0 → 2460.0 に自動的に伸びる。これは意図どおりの波及（verify に許す時間を増やした分、
失踪判定までの猶予も比例して増える）であり、追加の修正は不要。

### 変更しないことを明示する対象

- `tools/kiro-project/kiro-project.py` の git 自己修復ロジック（`GitBus`/`StateGitBus`、
  `_origin_matches`、`daemon_lock_key` 等）
- `tools/kiro-project/tests/test_kiro_project.py`、`tools/kiro-flow/tests/test_kiro_flow.py`
  （`verify_timeout` への参照はテストコード内に一件もなく、結合リスクはない）

## 3. 明示的な禁止事項（方針として厳守）

以下は**症状の握り潰しであり、本修正方針として一切採用しない**。将来この run 系列や
関連バックログを引き継ぐ実装者・エージェントも遵守すること。

- **skip**: `@pytest.mark.skip` 等で対象4テスト（`test_managed_bus_clone_is_reused`／
  `test_stale_index_lock_recovered_on_reuse`／`test_corrupt_index_clone_is_rebuilt`／
  `test_interrupted_rebase_recovered_on_reuse`）やその他のテストを無効化すること。
- **xfail**: `@pytest.mark.xfail` で期待失敗として黙認すること。
- **OS 分岐による回避**: `sys.platform == "darwin"` 等の判定でテスト本体や本体ロジックの
  挙動を分岐させ、macOS 環境でのみ異なる（緩い）検証にすり替えること。

これらはいずれも「4件失敗」という当初症状に対する対症療法であり、実際の原因
（verify タイムアウト設定不足）を放置したまま見かけ上のグリーンを作る。本タスクで採用した
修正（`verify_timeout` の既定値引き上げ）は、原因そのものを是正するものであり、
上記のいずれにも該当しない。

## 4. 範囲外として申し送る事項（今回は修正しない）

いずれも t7/t9 が「実装読解では裏付けがあるが、今回の症状を引き起こした証拠はない」として
報告した潜在リスク。本タスクのスコープ（gate1 が扱った4仮説への対応）には含まれないため、
修正は行わず、将来の別タスクとして backlog 化を検討する候補として記録するに留める。

1. **t7 発見**: `os.path.realpath()` は APFS 上で大文字小文字を正規化しない
   （`kiro-flow.py:1241-1244` の `_origin_matches`、`kiro-project.py:5115-5116` の同一remote判定）。
   `os.path.samefile()` 等への置き換えが望ましいが、実害の証拠なし。
2. **t9 発見**: `_clone_once()` と `StateGit` の初回 clone が `_git_env()`/`_env()` の
   環境変数サニタイズを経由しない（`env=` 未指定）。実害の証拠なし。

## 5. 完了条件との整合

完了条件コマンド `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q` は、
本修正の適用前から独立4回の実測ですべて `exit 0`（900 passed / 0 failed）である。
本修正（`verify_timeout` 引き上げ）はテストスイート自体を変えるものではなく、
**このコマンドを kiro-project 自身の verify ゲートとして呼び出したときに偽 NG を出さないようにする**
ためのものであり、完了条件そのものの充足には影響しない（既に充足済み）。
