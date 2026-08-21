# extract_statements Repair Loop

Use this reference when semantic review finds repairable blocking issues. The
main agent proposes the repair and reviews candidates. Python tools locate
context, build prompts, run the selected engine, normalize candidates, and apply
approved merges.

## Boundaries

- Main agent decides: repair scope, user confirmation, candidate acceptance, and
  final continuation.
- Python tools execute: context location, prompt construction, API or Claude CLI
  rerun, candidate storage, candidate packet creation, deterministic merge.
- Repair candidates are sidecar outputs until `apply-repair` receives an
  approved decision.
- Do not hand-write repair prompts or canonical cache edits.

## Flow

1. Semantic review records blocking findings and candidate repair items.
2. Main agent presents the issue and proposed repair intent to the user.
3. If the user confirms, write `repair_intent` with `user_confirmed: true`.
4. Run `rerun-extract-statements --repair-intent <json-or-path>`.
5. Build and read the candidate review packet.
6. Write `candidate_review_extract_statements`.
7. If approved, call `apply-repair`.
8. Validate `extract_statements` again.
9. Rebuild the full review packet and run semantic review again before
   continuing to `clean_nodes`, then `split_nodes`.

If the user declines repair, write `pause`; only then write the run report.

## Repair Intent Shape

```json
{
  "stage": "extract_statements",
  "decision": "repair_intent",
  "user_confirmed": true,
  "repair_intent": {
    "stage": "extract_statements",
    "source_block_key": "0",
    "issue_type": "missing_nodes",
    "severity": "blocking",
    "evidence": "The reviewed source contains a missing theorem unit.",
    "anchor_texts": ["Theorem 1.2", "(1.2)"],
    "expected_labels": ["(1.2)"],
    "affected_node_indices": [],
    "context_policy": "localized_window"
  },
  "reason": "User confirmed repair after blocking semantic review."
}
```

`anchor_texts` and `expected_labels` are evidence hints only. They do not
authorize the model to invent nodes not supported by localized source text.

## Candidate Review Shape

```json
{
  "stage": "extract_statements",
  "decision": "candidate_review_extract_statements",
  "repair_id": "repair-...",
  "approved": true,
  "findings": [],
  "reason": "The candidate restores the missing local mathematical unit without corrupting neighboring nodes."
}
```

Reject the candidate when it invents content, drops valid existing nodes,
misassigns proof, changes unrelated labels, or fails to cover the confirmed
repair intent.

After rejecting a candidate, do not create the next repair intent automatically.
Explain the rejection findings and proposed new repair scope to the user. The
next `repair_intent` must include `user_confirmed: true` and
`supersedes_repair_id` for the rejected candidate.

For the same `source_block_key` plus `issue_type`, stop after two rejected
candidates and ask the user whether to pause, expand context, or materially
change the repair goal. Do not keep generating same-scope candidates.

## Apply Decision Shape

```json
{
  "stage": "extract_statements",
  "decision": "apply_repair",
  "repair_id": "repair-...",
  "approved": true,
  "reason": "Approved candidate should replace the covered local extraction."
}
```

Unlabeled candidates require explicit `affected_node_indices` or
`approve_append_unlabeled: true`. The tool must back up canonical cache before
merging and record repair history.

## Engines

Repair runs support the same prompt contract for API and Claude CLI. If the user
does not request Claude CLI, use API mode. If Claude CLI is requested, pass
`--llm-engine claude_cli` to the repair command.
