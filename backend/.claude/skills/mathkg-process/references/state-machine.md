# MathKG State Machine

Use this reference when `next-action` returns a non-trivial state. The Python
tool reports deterministic transitions from artifacts and recorded decisions;
the main agent still owns semantic judgment.

## Core Rules

- Execute only the returned `next_action`.
- Prefer the returned `suggested_command`; do not reconstruct an equivalent command by hand.
- If `action_kind` is `ask_user`, ask the user and do not run a stage command.
- If `action_kind` is `agent_decision`, read the cited evidence once and write the required decision.
- After each decision or artifact, call `next-action` once.
- Continue only when `state_fingerprint` changed.
- If `repeat_guard.triggered` is true, stop and report the pending action.
- Never invoke `Skill("mathkg-process")`, `/mathkg-process`, or this skill from itself.
- Never use TaskCreate, TaskUpdate, or a task list as the pipeline state machine.
- Never emit repeated progress text that does not name the concrete stage/action being performed.
- Never skip candidate review or apply a repair without an approved decision.

## Transition Guide

| `orchestration_state` | Main-agent action | Typical command | Required decision | Forbidden action |
| --- | --- | --- | --- | --- |
| `needs_stage_run` | Decide whether to run the current stage. | `run-stage --stage <stage>` or `write-agent-decision` | `run_stage` or `reuse_cache` | Running downstream first |
| `needs_stage_structural_validation` | Validate an existing cache before trusting or reusing it. | `validate-stage --stage <stage>` | None before tool execution | Writing semantic approval from file existence alone |
| `stage_quality_needs_agent_judgment` | Read validation facts and stage output, then decide continue/rerun/pause/manual review. | `validate-stage --stage <stage>` then `write-agent-decision` | `continue`, `rerun_stage`, `pause`, or `manual_review` | Treating structure-only validation as semantic approval |
| `failed_stage_tasks_need_rerun` | Rerun only the failed task keys for the stage reported by the tool. | Use the returned `suggested_command`, e.g. `rerun-failed-tasks --stage <stage>` | None before tool execution | Reusing old downstream cache or rerunning every successful task |
| `needs_extract_statements_semantic_review` | Validate, build packet, read every chunk, and write a full semantic review. | `validate-stage`, `build-review-packet`, `write-agent-decision` | `semantic_review_extract_statements` | Sampling chunks |
| `blocking_review_needs_user_confirmation` | Show blocking findings and proposed repair scope to the user. | `write-agent-decision` only after user answer | `repair_intent` with `user_confirmed: true`, or `pause` | Calling `rerun-extract-statements` before confirmation |
| `repair_intent_needs_candidate` | Generate a sidecar repair candidate from the recorded intent. | `rerun-extract-statements --repair-intent <json-or-path>` | None before tool execution | Hand-writing repair prompts |
| `candidate_generated_needs_review` | Build/read candidate evidence and decide whether it is acceptable. | `build-candidate-review-packet`, `write-agent-decision` | `candidate_review_extract_statements` | Applying without review |
| `candidate_reviewed_needs_apply` | Apply only the approved candidate decision. | `apply-repair --repair-id <id> --decision <json-or-path>` | Existing approved candidate review | Editing canonical cache directly |
| `user_declined_repair_needs_report` | Finalize a paused report. | `write-run-report` | Existing `pause` decision | Generating a repair candidate |
| `final_report_needs_write` | Write final report. | `write-run-report` | None | Reporting without tool-generated report |

## Decision Values

Use one of these values in `write-agent-decision`:

- `frontier_assessment`
- `reuse_cache`
- `run_stage`
- `continue`
- `rerun_stage`
- `expand_context_rerun`
- `manual_review`
- `pause`
- `semantic_review_extract_statements`
- `repair_intent`
- `candidate_review_extract_statements`
- `apply_repair`
- `reject_repair`

## Structural Gate

Treat these as blocking unless there is a clear reason they are harmless:

- Missing expected output for the current stage.
- Invalid JSON in required artifacts.
- Empty output where non-empty upstream input exists.
- Missing required fields in many records.
- Invalid node or edge references.
- Severe count collapse between upstream and downstream artifacts.
- Repeated validation issues after one rerun.

Warnings that require judgment include low title coverage, localized logic
render errors, localized unresolved references, localized predicate conflicts,
and empty relation graphs for tiny or relation-free documents.
