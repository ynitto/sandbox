---
description: "Document graph pipeline: save (Excel/PDF→Neo4j) or search (GraphRAG query)"
allowed-tools: Bash, Read, Write
---

You are running the document graph pipeline. The argument is: $ARGUMENTS

## Dispatch rules

Parse $ARGUMENTS to determine the mode:

- **save mode** — argument is a file path (ends with `.pdf`, `.xlsx`, `.xls`, `.xlsm`)
- **search mode** — argument is a quoted or plain text query (not a file path)
- **no argument** — show usage and stop

---

## Save mode

Goal: ingest the document, extract tables via Table Transformer, build Document AST,
and load into Neo4j.

### Step 1 — Check dependencies
```bash
pip install -q -r pipeline/requirements.txt
```

### Step 2 — Dry-run (inspect AST before loading)
```bash
python -m pipeline.pipeline save "$FILE" --dry-run
```
Summarize: number of sections / tables / paragraphs found.
If no tables were detected, warn the user.

### Step 3 — Ask for Neo4j connection (if not supplied in $ARGUMENTS)
Prompt the user:
> Neo4j bolt URI? (default: bolt://localhost:7687)
> Username? (default: neo4j)
> Password?

### Step 4 — Load
```bash
python -m pipeline.pipeline save "$FILE" \
  --neo4j "$NEO4J_URI" --user "$NEO4J_USER" --password "$NEO4J_PASS"
```
Report: "Loaded N sections, M tables into Neo4j."

---

## Search mode

Goal: run a full-text + graph-traversal query against the loaded document graph.

### Step 1 — Ask for Neo4j connection (if not supplied in $ARGUMENTS)
Prompt the user:
> Neo4j bolt URI? (default: bolt://localhost:7687)
> Username? (default: neo4j)
> Password?

### Step 2 — Search
```bash
python -m pipeline.pipeline search "$QUERY" \
  --neo4j "$NEO4J_URI" --user "$NEO4J_USER" --password "$NEO4J_PASS" \
  --limit 10
```

### Step 3 — Present results
- For each **Cell hit**: show document › section › sheet/page › column header › cell text
- For each **Paragraph hit**: show document › section › page › text snippet
- If zero hits: suggest reformulating the query or checking that the document was saved first.

---

## Usage examples (shown when $ARGUMENTS is empty)

```
/graph-pipeline data.xlsx
/graph-pipeline report.pdf
/graph-pipeline "revenue Q3 2024"
/graph-pipeline save report.pdf --neo4j bolt://localhost:7687 --user neo4j --password s3cr3t
/graph-pipeline search "operating profit" --neo4j bolt://localhost:7687
```
