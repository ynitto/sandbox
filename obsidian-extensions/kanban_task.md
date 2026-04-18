---
title: <% tp.file.title %>
created: <% tp.date.now("YYYY-MM-DD HH:mm") %>
status: Todo
task_id:
priority: 2
category:
tags: [task]
---

```button
name 🤖 Send to Kiro
type command
action Shell commands: Send to Kiro
color blue
```

^send-to-kiro

```button
name 📋 分類して登録
type command
action Shell commands: Classify and Enqueue
color green
```

^classify-enqueue

```button
name 📥 結果を取得
type command
action Shell commands: Sync Result from Queue
color yellow
```

^sync-result

```button
name 🔍 レビュー依頼
type command
action Shell commands: Request MR Review
color purple
```

^request-review

## タスク概要
<% tp.system.prompt("タスク内容を入力") %>

## 受け入れ条件
- [ ] 

## 参考スキル
<!-- 自動追記: 関連スキルへの [[wikiリンク]] -->

## 実行結果
<!-- 自動追記: Kiro 実行後に write_back_result.py が追記 -->

## メモ

## Git情報
```git-manager
show: all
```
