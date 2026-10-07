# PC 版のメイン画面（設計書と TSX の画面）

設計書の側から画面の仕様を変え、React（TSX）の実装を合わせる。途中でコミットしたり、よくある名前のローカル変数を
変えたりしたときの測り方を見る。2026-10-05 に通し、見つけた不具合を直した（PR #911）。

## サンプル

作業用のフォルダに 2 つのリポジトリを並べて作る。どちらも `main` で始め、作ったものをコミットしてから codd を入れる。

**app（実装）**

- `src/components/DesktopPreviewWorkspace.tsx` — `const disabled = !canConfirm(props.items)` で印刷ボタン（`PrintButton`）の可否を決め、
  確定ボタン（`ConfirmButton`）が `handleDesignGenerationNext` を呼ぶ。生成中は `Dialog_Generating_next` を出す
- `src/components/ZoomControl.tsx`・`src/components/PageNav.tsx` — どちらも `disabled` を受け取るだけの部品
- `src/lib/selection.ts` — `canConfirm(items)` と `isSelectableCandidate(c)`
- `src/components/MobilePrint.tsx` — 別の画面の、同じ名前の部品 `function PrintButton`
- `tests/selection.test.ts`（`canConfirm` を確かめる）・`tests/workspace.test.tsx`（描画するだけ）

**design（設計書）**

- `docs/screens/pc-main.md` — 見出しは目的・画面・操作。確定ボタン（`handleDesignGenerationNext`）で次へ進む、印刷ボタンは
  `canConfirm` が偽のとき押せない、生成中は `Dialog_Generating_next` を出す
- `docs/screens/zoom.md`・`docs/screens/mobile.md` — 同じ見出しで、「`disabled` のときボタンを押せない」のような行がある

**codd を入れる**: design は `--side design --ref ../app`、app は `--side impl --ref ../design`。テストのコマンドは置かない。

## 1. 確定ボタンをなくす

**頼むこと**（design で）: PC 版メイン画面から確定ボタンをなくし、候補を選んだら次へ進むようにして。印刷ボタンは生成済みのときだけ押せる
（`isGeneratedPrintDisabled`）。

**エージェントの判断**: 実装は `const disabled = isGeneratedPrintDisabled;` のように、`disabled` という変数を残したまま中身だけ変える。
`tests/selection.test.ts` は `canConfirm` を変えないので変更不要。最初はテストの変更案の `tests/workspace.test.tsx` を変え忘れる。

**期待**

- 計画の検査が `tests/selection.test.ts` を「未判断」として書き足す。変更不要とすれば通る
- テストを変え忘れると、変えたあとの検査が「テストをまだ変えていません」で止め、advise は訊かずにやり直す（AUTO APPLY）
- 中身だけ変えたローカル変数 `disabled` を検索語にせず、関係の無い `zoom.md`・`mobile.md` を「直していない」に挙げない
- 実装でコンポーネントの中にハンドラ（`function handleSelect`）を足しても、「新しく足した名前を確かめるテストがありません」に数えない
  （外から呼べない名前）
- 変える段で設計書に `PrintButton` と書き足すと、別の画面の `MobilePrint.tsx` に当たって止まる（AUTO APPLY）。`.codd/apply.md` の
  「計画との違い」に名前ごとに「関係なし」と書けば、その名前だけで当たったファイルはまとめて済む

## 2. 続けてもう 1 回

**頼むこと**（design で、1 を記録してコミットしたあと）: ズームは `ZoomControl` で 2 段まで拡大できると書いて。

**期待**: 設計書だけの計画で通る。前の回の記録（`.plans/`）は書き換えない。

## 3. 変える段の途中でコミットする

**頼むこと**: 1 と同じ（1 で変えたファイルを、サンプルを作ったときの中身に戻すコミットをしてから。計画の記録は残す）。
ただし変える段の途中で、app のリポジトリをいったんコミットする。

**期待**: 途中のコミットに入った変更も、この回の変更として影響範囲を測る（黙って通らない）。`rollback` は
「途中でコミットされたので戻せません（git で戻してください）」と答える。報告は、app を途中でコミットしたと書く
（「どのリポジトリもコミットしていない」と書かない）。
途中のコミットに計画に無いファイルの変更が入っていて止まったら、そのファイルを作業中に元へ戻せば（コミットしなくても）通る。
