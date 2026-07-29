# agent-tools による業務改善：図作成プロンプト

## 再生成プロンプト

```text
Use case: infographic-diagram
Asset type: 16:9 landscape presentation slide for explaining agent-tools–driven business process improvement
Primary request: Create a polished Japanese business architecture diagram titled "agent-toolsによる業務改善". Show agent-dashboard as the single front end for all development and operations. Put GitLab at the center as a messaging hub, while making it visually clear that humans and agents access GitLab as little as possible because agent-dashboard aggregates and summarizes information.

Scene/backdrop: clean off-white background, generous whitespace, no decorative scenery
Style/medium: crisp vector-like enterprise infographic, flat shapes, thin connector lines, restrained modern design, immediately readable in a presentation
Composition/framing: 16:9 landscape. Use a hub-and-spoke structure with five clearly separated regions:
1. TOP: a wide dark navy header/interface layer labeled "agent-dashboard" with subtitle "開発・業務のフロントエンド". All users enter through this layer.
2. CENTER: a compact orange-red hexagonal hub labeled "GitLab" and beneath it "メッセージングハブ". Surround it with a thin dashed boundary labeled "直接アクセスは最小限". Connect it to agent-dashboard and agent tools using thin bidirectional arrows; do not show humans directly operating GitLab.
3. LEFT: project work region labeled "プロジェクト業務" containing a blue rounded container labeled "agent-project". Inside show a horizontal autonomous flow: "要件承認" → "計画承認" → "AI自律実行" → "レビュー". Human icons appear only above the three approval/review gates: "要件承認", "計画承認", "レビュー". Agent/robot icons own "AI自律実行" and prepare a small card labeled "レビュー用要約" before review. Add a small caption "承認とレビュー以外はエージェントに任せる".
4. RIGHT: recurring work region labeled "定常業務". Show agent-dashboard doing "分析・定型化" then branching into two equal execution modes: a teal loop labeled "agent-loop / 定期起動" and a teal lightning button labeled "アドホック実行". Add caption "どちらもセットアップ".
5. BOTTOM: teamwork outcome region labeled "チーム活用" with four connected people icons and four compact outcome chips: "人材育成", "モチベーション", "スキルアップ", "チーム内外への共有". Arrows from summarized results in agent-dashboard flow down to this region.

Flow semantics: Humans → agent-dashboard. agent-dashboard orchestrates agent-project and recurring work. agent-project and agent-loop exchange minimal messages through GitLab. Agents return concise summaries and review-ready evidence to agent-dashboard. Team members consume summaries and shared knowledge through agent-dashboard. Use thicker arrows for primary workflow through agent-dashboard, thin arrows for GitLab messaging, and dashed arrows for summaries/knowledge sharing.

Color palette: navy #17324D for agent-dashboard, blue #2F6FED for project work, teal #18A999 for recurring work, orange-red #E66A3C for GitLab, warm yellow #F4C95D for human approval gates, light gray containers, charcoal text
Typography: highly legible Japanese sans-serif, large section headings, short labels only
Text (verbatim): "agent-toolsによる業務改善", "agent-dashboard", "開発・業務のフロントエンド", "GitLab", "メッセージングハブ", "直接アクセスは最小限", "プロジェクト業務", "agent-project", "要件承認", "計画承認", "AI自律実行", "レビュー", "レビュー用要約", "承認とレビュー以外はエージェントに任せる", "定常業務", "分析・定型化", "agent-loop / 定期起動", "アドホック実行", "どちらもセットアップ", "チーム活用", "人材育成", "モチベーション", "スキルアップ", "チーム内外への共有"
Constraints: render every listed label exactly once except "agent-dashboard" may also appear in flow context if needed; no extra prose; no garbled characters; no people directly connected to GitLab; GitLab must remain visually central but smaller than agent-dashboard; maintain strong hierarchy and generous whitespace; all arrows must have obvious direction; no logos except the plain text name GitLab; no trademarks as icons; no watermark
Avoid: dense tiny text, photorealism, 3D, gradients, shadows, decorative illustrations, tangled connector lines, duplicated labels, English explanatory text
```

## 再生成時の確認点

- 人間の介在は「要件承認」「計画承認」「レビュー」の3点だけ。
- agent-dashboard が唯一の入口で、人間から GitLab への直接線は引かない。
- GitLab は中央に置くが、agent-dashboard より小さくする。
- agent-project の自律実行からレビュー前に「レビュー用要約」を出す。
- 定常業務は「定期起動」と「アドホック実行」の両方を示す。
- チーム活用には、人材育成・モチベーション・スキルアップ・チーム内外への共有を含める。

画像生成は確率的なため画素単位では一致しませんが、このプロンプトで構成・文言・配色・矢印の意味を再現できます。
