# Architecture — Mission `g`

## Goal
Deliver mission `g` with complete role artifacts. Acceptance: artifacts present and consistent.

## Scope
- In scope: design (`architecture.md`), implementation by `impl`, integration by `integrator`, review by `reviewer`.
- Out of scope: anything not required to assemble a valid `deliverable/` for mission `g`.

## Context
- Design doc is canonical; no owner decisions recorded.
- No cross-role messages yet; proceed with a minimal viable design.

## Architecture
```
owner
  └─ mission g
       ├─ architect → artifacts/architecture.md
       ├─ impl      → implementation artifact(s)
       ├─ reviewer  → review artifact(s)
       └─ integrator → deliverable/ (verified bundle)
```

### Components
| Role | Responsibility | Artifact |
|------|----------------|----------|
| architect | Define structure, boundaries, acceptance mapping | `architecture.md` |
| impl | Implement against this design | impl artifacts |
| reviewer | Verify design/impl fit and quality | review artifacts |
| integrator | Validate all artifacts; assemble `deliverable/` | deliverable bundle |

### Boundaries
- Architect owns design only; does not implement or integrate.
- Impl follows this document; conflicts → escalate to owner via `decision-request`.
- Integrator is the single assembler of `deliverable/`.

## Acceptance mapping
| Criterion | How satisfied |
|-----------|----------------|
| Artifacts complete | Each role writes its required artifact; integrator confirms presence |

## Risks & assumptions
- Assumption: mission label `g` has no further functional requirements beyond artifact completion.
- Risk: hidden requirements appear later → owner decision required; revise this doc.

## Handoff to impl
1. Treat this file as the design baseline.
2. Produce the implementation artifact(s) needed for mission `g`.
3. Do not invent extra scope without owner decision.
