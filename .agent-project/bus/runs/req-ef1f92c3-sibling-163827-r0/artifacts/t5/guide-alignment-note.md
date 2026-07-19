# GUIDE.md の追随結果（t5）

**切り口: 手順の実体を GUIDE に複製せず、README を単一の正本として参照させる。**
GUIDE には「いつ・なぜ入れるか」の導線だけを置いた。両方に手順を書けば、今回直しているドリフトを
そのまま作り直すことになるため。

## 変更した4箇所（すべて `tools/agent-project/GUIDE.md`）

| 箇所 | 内容 |
|---|---|
| L2「安全装置の役割」直後 | 「一貫性ゲート（opt-in）」段落を新設。経路2つ（yaml 直書き／`codd_gate_regression.py` の1行注入）を示し、貼る値は README を参照させる |
| L3 `doctor` 節 | 結線の確認手段が `doctor` であることと、検出がパッケージ外の `codd_gate_wiring.py` にあることを追記 |
| 「安全装置の早見表」 | `一貫性ゲート` 行を `回帰ゲート` の直下に追加 |
| 「設定の決め方・早見表」 | `intake_cmd` / `intake_interval` 行を追加。`regression_cmd` 行に一貫性ゲートも載る旨を追記 |

## build_config によるメモリ自動配線の削除 → 対象なし

GUIDE.md に該当記述は元から存在しなかった（`grep -n "build_config\|自動配線\|_apply_codd_gate_auto_wiring" GUIDE.md`
＝0件。t1 の棚卸しとも一致）。**削除ではなく「無いことの確認」として処理した。**
逆に GUIDE には codd-gate の導線自体が皆無だったため、タスクの主眼を「削除」から「不足分の追加」へ置き換えている。

## 呼称の統一

README 側の呼称をそのまま採用した。GUIDE に新語を作っていない。

- 一貫性ゲート（codd-gate 連携の総称）
- 差分ゲート（`regression_cmd` に載る `codd-gate verify`）／ 回帰ゲート（`regression_cmd` 一般）
- 取り込みコマンド＝`intake_cmd`

`回帰ゲート` / `検収ゲート` / `パス保護` / `flake` は変更前から両ドキュメントで一致していた。

## 後続への申し送り

- 設計書 §4.1 を直すタスクは、GUIDE が README を参照する構造にしたことを踏まえ、
  **設計書にも手順を複製しない**でよい（正本は README の一貫性ゲート項）。
- GUIDE から README への参照は節見出し「フレーク耐性 / 回帰 / 検収 / パス保護」を名指ししている。
  README 側でこの見出しを改名するなら GUIDE:131 も併せて直すこと。
