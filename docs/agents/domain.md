# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- `CONTEXT.md` at the repo root
- `docs/adr/` for ADRs related to the area being changed

If these files do not exist yet, proceed silently. Do not flag their absence or suggest creating them upfront.

## File structure

This repo is configured as a single-context project:

```text
/
|-- CONTEXT.md
|-- docs/
|   `-- adr/
`-- src/
```

If the codebase grows into multiple contexts later, this setup can be changed to a root `CONTEXT-MAP.md` plus per-context `CONTEXT.md` files.

## Use the glossary's vocabulary

When naming domain concepts in issues, refactor proposals, hypotheses, or tests, use the terms defined in `CONTEXT.md`.

If a needed concept is not in the glossary yet, either reconsider the term or note the gap for `/domain-modeling`.

## Flag ADR conflicts

If proposed work contradicts an existing ADR, surface that conflict explicitly instead of silently overriding it.
