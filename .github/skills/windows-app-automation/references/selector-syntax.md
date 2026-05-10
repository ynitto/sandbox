# winauto Selector Syntax Reference

## 目次

- [Overview](#overview)
- [Single Selectors](#single-selectors)
- [Control Type Values](#control-type-values)
- [Chained Selectors](#chained-selectors)
- [pywinauto Code Equivalents](#pywinauto-code-equivalents)
- [Selector Stability Ranking](#selector-stability-ranking)
- [Key Sequences](#key-sequences-for-winauto-keys-and-type_keys)
- [Inspection Workflow](#inspection-workflow)
- [Common App Types and Recommended Backends](#common-app-types-and-recommended-backends)

## Overview

Selectors identify UI elements within a Windows application window.
They are used by `winauto` CLI commands and can be composed via chaining.

---

## Single Selectors

| Syntax | pywinauto kwarg | Example | Notes |
|--------|----------------|---------|-------|
| `auto_id:=VALUE` | `auto_id` | `auto_id:=btnOK` | **Most stable**. Use when AutomationID is set. |
| `name:=VALUE` | `title` | `name:=Submit` | Matches window text / label. Partial matches not supported. |
| `control:=TYPE` | `control_type` | `control:=Button` | Control type string (see table below). |
| `class:=VALUE` | `class_name` | `class:=Edit` | Win32 class name (reliable for Win32 apps). |
| `text:=VALUE` | `title` | `text:=Click me` | Alias for `name:=`. Use for button labels / static text. |
| `index:=N` | `found_index` | `index:=0` | 0-based position among matched siblings. |

---

## Control Type Values

Use with `control:=TYPE`. Case-insensitive in the CLI.

| Short name | Full type | Description |
|-----------|-----------|-------------|
| `Button` | `Button` | Push buttons, icon buttons |
| `Edit` | `Edit` | Text input fields |
| `Text` / `Static` | `Text` | Read-only labels |
| `CheckBox` | `CheckBox` | Checkboxes |
| `RadioButton` | `RadioButton` | Radio buttons |
| `ListBox` | `ListBox` | List boxes |
| `ListItem` | `ListItem` | Items within a list |
| `ComboBox` | `ComboBox` | Dropdown menus |
| `Tree` | `Tree` | Tree views |
| `TreeItem` | `TreeItem` | Items within a tree |
| `Menu` | `Menu` | Menu bars |
| `MenuItem` | `MenuItem` | Individual menu items |
| `ToolBar` | `ToolBar` | Toolbars |
| `Tab` | `Tab` | Tab control containers |
| `TabItem` | `TabItem` | Individual tabs |
| `Pane` | `Pane` | Container panes |
| `Dialog` | `Dialog` | Dialog windows |
| `Window` | `Window` | Top-level windows |
| `GroupBox` | `Group` | Group boxes |
| `ScrollBar` | `ScrollBar` | Scroll bars |
| `Slider` | `Slider` | Slider controls |
| `ProgressBar` | `ProgressBar` | Progress bars |
| `Image` | `Image` | Image controls |
| `Document` | `Document` | Rich text / document areas |
| `Custom` | `Custom` | Custom controls |

---

## Chained Selectors

Use `>>` to traverse the element hierarchy. Each part narrows the search to children of the previous match.

```
PARENT_SELECTOR >> CHILD_SELECTOR >> GRANDCHILD_SELECTOR
```

**Examples:**

```bash
# Find OK button inside a specific panel
"auto_id:=MainPanel >> control:=Button >> name:=OK"

# Find the first Edit in the form group
"name:=Login Form >> control:=Edit >> index:=0"

# Find a menu item
"control:=Menu >> name:=File >> control:=MenuItem >> name:=Save"
```

---

## pywinauto Code Equivalents

Each selector maps directly to pywinauto's `child_window()` call:

```python
# name:=Submit
win.child_window(title="Submit")

# auto_id:=btnOK
win.child_window(auto_id="btnOK")

# control:=Button
win.child_window(control_type="Button")

# class:=Edit
win.child_window(class_name="Edit")

# index:=1
win.child_window(found_index=1)

# Chained: "auto_id:=Panel >> control:=Button >> name:=OK"
win.child_window(auto_id="Panel") \
   .child_window(control_type="Button") \
   .child_window(title="OK")
```

---

## Selector Stability Ranking

Choose selectors in this order for most stable automation:

1. **`auto_id:=`** — AutomationID assigned by developer. Survives UI refactors.
2. **`name:=` + `control:=`** — Name + control type combination. More specific.
3. **`name:=`** — Name alone. May change with localization.
4. **`class:=`** — Win32 class name. Good for classic Win32 apps.
5. **`control:=` + `index:=`** — Positional. Breaks if order changes.

---

## Key Sequences (for `winauto keys` and `type_keys`)

Used with `--method keys` or the `keys` command.

| Notation | Key |
|---------|-----|
| `{ENTER}` | Enter |
| `{ESC}` | Escape |
| `{TAB}` | Tab |
| `{SPACE}` | Space |
| `{BACK}` | Backspace |
| `{DEL}` | Delete |
| `{UP}` / `{DOWN}` / `{LEFT}` / `{RIGHT}` | Arrow keys |
| `{HOME}` / `{END}` | Home / End |
| `{F1}`…`{F12}` | Function keys |
| `^c` | Ctrl+C |
| `^v` | Ctrl+V |
| `^a` | Ctrl+A (select all) |
| `^s` | Ctrl+S (save) |
| `^z` | Ctrl+Z (undo) |
| `^+s` | Ctrl+Shift+S |
| `%{F4}` | Alt+F4 (close) |
| `%d` | Alt+D (focus address bar) |
| `+{TAB}` | Shift+Tab |

---

## Inspection Workflow

Before writing automation, always inspect the element tree:

```bash
# Step 1: Find running apps
python scripts/element_inspector.py --list

# Step 2: Inspect the app
python scripts/element_inspector.py --app notepad --depth 5

# Step 3: Inspect a sub-tree
python scripts/element_inspector.py --app notepad --selector "control:=Document"

# Step 4: Get JSON for programmatic use
python scripts/element_inspector.py --app notepad --json > tree.json
```

or via winauto CLI:

```bash
python tools/winauto/winauto.py tree --app notepad
python tools/winauto/winauto.py tree --app notepad --selector "control:=MenuBar" --depth 2
```

---

## Common App Types and Recommended Backends

| App type | Backend | Notes |
|---------|---------|-------|
| Classic Win32 (MFC, WTL, VCL) | `win32` or `uia` | `win32` first; fall back to `uia` |
| WPF / XAML | `uia` | AutomationIDs usually available |
| WinForms | `uia` | Use `uia`; `win32` for older .NET |
| UWP / Windows Store | `uia` | Only UIA works |
| Electron (wrapped web) | `uia` | Limited; consider CDP for full control |
| Qt | `uia` | Needs Qt Accessibility plugin enabled |
| Java Swing/AWT | `uia` | Needs Java Access Bridge enabled |
| SAP GUI | `win32` | SAP has its own scripting API too |
