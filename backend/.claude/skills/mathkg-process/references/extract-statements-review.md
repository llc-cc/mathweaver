# extract_statements Semantic Review

Use this reference after `extract_statements` exists or has just run. Structural
validation is only a safety floor; semantic review decides whether the extracted
nodes preserve the mathematical units in the source.

## Required Flow

1. Run `validate-stage --stage extract_statements`.
2. Run `build-review-packet --stage extract_statements`.
3. Read every chunk in the manifest. Do not sample.
4. Compare each source block with its extracted nodes.
5. Write one `semantic_review_extract_statements` decision.
6. If blocking findings are repairable, ask the user to confirm the proposed
   repair intent before any rerun.

## What To Check

- Obvious definition, theorem, lemma, proposition, corollary, example, remark,
  or assumption units missing from extraction.
- `content` missing hypotheses, assumptions, conclusions, defined objects, or
  important quantified conditions.
- `proof` attached to the wrong node, copied from another proposition, copied
  from a reference-only proof, or extracted from an empty proof heading.
- `label` copied from a citation, section number, or `Proof of ...` phrase
  rather than the current logical unit heading.
- `node_type` inconsistent with the source semantics.
- Multiple logical units merged into one node.
- One logical unit split into misleading fragments.
- Warnings that are local and acceptable versus warnings that indicate
  systematic extraction drift.

## Blocking Findings

Treat these as blocking by default:

- High-confidence missing theorem, definition, lemma, proposition, corollary, or
  other key mathematical unit.
- Missing theorem assumptions or conclusions that would change downstream logic.
- Repeated proof misassignment across the document or chunk.
- Systematic label drift, such as labels copied from citations.
- Source blocks with several logical units but only one merged node.
- Empty or generic content for non-empty mathematical source.

Blocking findings should not end the run. Present the finding and proposed
repair scope to the user, then wait for confirmation.

## Non-Blocking Manual Review

These can usually be recorded and allowed to continue when localized:

- Slightly awkward title-like labels that do not break identity.
- A single suspicious proof boundary with enough surrounding evidence preserved.
- Minor wording compression that keeps assumptions and conclusions intact.
- Local ambiguity where downstream stages can still operate safely.

Record these in `manual_review_items` with source block, node index or label,
issue type, evidence, and why continuation is acceptable.

## Decision Shape

```json
{
  "stage": "extract_statements",
  "decision": "semantic_review_extract_statements",
  "review_scope": "full",
  "reviewed_chunks": ["path/to/chunk_0001.json"],
  "semantic_findings": [],
  "blocking_findings": [],
  "manual_review_items": [],
  "candidate_rerun_items": [],
  "candidate_expand_context_items": [],
  "next_action": "continue",
  "reason": "Reviewed every packet chunk and found no blocking extraction drift."
}
```

Use `candidate_rerun_items` for repairs that must block downstream execution.
Use `candidate_expand_context_items` for optional expanded-context cleanup
ideas. Expanded-context items do not block the pipeline unless the same problem
is also represented in `blocking_findings` or `candidate_rerun_items`.
