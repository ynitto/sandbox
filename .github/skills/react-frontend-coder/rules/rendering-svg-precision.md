---
title: SVGの精度を最適化する
impact: LOW
impactDescription: ファイルサイズを減らす
tags: rendering, svg, optimization, svgo
---

## SVGの精度を最適化する

ファイルサイズを小さくするには、SVG 座標の精度を下げます。最適な精度は viewBox のサイズによって異なりますが、一般的には精度を下げることを考慮する必要があります。

**誤り（精度が高すぎる）:**

```svg
<path d="M 10.293847 20.847362 L 30.938472 40.192837" />
```

**正しい例（小数点第 1 位）:**

```svg
<path d="M 10.3 20.8 L 30.9 40.2" />
```

**SVGO で自動化する:**

```bash
npx svgo --precision=1 --multipass icon.svg
```
