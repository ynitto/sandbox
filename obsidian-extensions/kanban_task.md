---
title: <% tp.file.title %>
created: <% tp.date.now("YYYY-MM-DD HH:mm") %>
status: Todo
---
---
title: <% tp.file.title %>
created: <% tp.date.now("YYYY-MM-DD HH:mm") %>
status: Todo
tags: [task]
---

```button
name 🤖 Send to Kiro
type command
action Shell commands: Send to Kiro
color blue
```

^send-to-kiro

## タスク概要
<% tp.system.prompt("タスク内容を入力") %>

## 受け入れ条件
- [ ] 

## メモ