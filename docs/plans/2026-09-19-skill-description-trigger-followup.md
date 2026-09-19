# 発動条件が書かれていない 5 本の description（積み残し）

2026-09-19。`quality_check.py` を CI へ載せ、description が 200 字を超えていた
14 本を削った作業（`fix:` / `ci:` / `docs:` の各コミット）の積み残しメモ。

## 何が残っているか

`quality_check.py` は description に発動のトリガー条件（「〜の場合」「〜とき」
「〜などで発動」等）が無いと警告する。現状 5 本が該当する。

| スキル | tier | いまの description の字数 |
|---|---|---|
| backlog-planner | experimental | 173 |
| backlog-verifier | experimental | 179 |
| flow-planner | experimental | 179 |
| persona-use | core | 126 |
| table-spec-extractor | deprecated | 149 |

いずれも 200 字は超えていないので、今回の「削る」作業とは対象が重ならない。
ただし backlog 系と flow-planner は余白が 20 字ほどしかないので、足すときは
既存の文を詰めることになる。table-spec-extractor は deprecated なので、
足すより先に「残すのか」を決めたほうがよい。

## 今回やらなかった理由

削る（情報を減らす）作業と足す（発動条件を書く）作業は、判断の種類も
レビューの見どころも違う。同じコミットに混ぜると、どちらの意図で文が
変わったのか差分から読めなくなる。

## 次にやるとき

- 5 本それぞれの本文（「使用するタイミング」相当の節）から発動条件を拾い、
  description の末尾へ 1 文で足す。200 字の枠内に収める。
- 足し終えた時点で `description` 系の警告は 0 になる。そこまで来たら、
  CI の `quality_check.py` を警告でも落とす設定にするかを別途判断する
  （いまは警告 59 件が残っているので、落とす設定にはしていない）。
- Issue 化するかどうかは人の判断に委ねる。このメモはその判断材料。
