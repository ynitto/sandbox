### docs: Teams–SharePoint ブリッジ設計の全体図を縦向きに直し、部品対応を明記

- `docs/plans/2026-11-23-teams-sharepoint-bridge-design.md` の Mermaid 図を横向き（flowchart LR）から縦向き（flowchart TB）に変え、実行場所ごとの subgraph（MS365 の中／ローカル PC／GitLab）に分けた。
- 各ブロックに、対応するプログラム・ファイルと Dagu 例のファイル名を添えた。
- §6「部品の対応」表を「図のブロック｜プログラム・ファイル｜Dagu 例｜動く場所」の4列に拡張し、図のブロック名と行を一致させた（図に無い整理（gc）の行も追加）。
