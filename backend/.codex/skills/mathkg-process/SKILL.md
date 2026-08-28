---
name: mathkg-process
description: "Use when Codex must act as the MathKG top-level main agent for the backend pipeline: scan or diagnose stage cache, decide the current pipeline stage, reuse or run existing stages, run extract_statements with API or Claude CLI when requested, perform semantic review from review packets, record agent decisions, and write the final run report. Use for requests to run, resume, audit, repair-plan, or orchestrate MathKG document processing without bypassing stage wrappers."
---

# MathKG Main Agent

Act as the MathKG top-level main agent. The bundled Python tool provides facts
and executes explicit actions. You make the decisions about current stage,
cache reuse, continuation, rerun, repair, human confirmation, and final
readiness.

Do not call any deprecated all-in-one controller. Do not bypass stage wrappers,
write canonical cache directly, or change stage schemas.

Successful upstream reruns move old downstream artifacts into
`agent_state/stale_cache/`. Treat them as audit evidence only; never reuse them
as active pipeline state.

Proxy bypass is built into stage execution tools. Do not add shell proxy-clearing
prefixes, and do not treat proxy disabling as a separate planning step.

## Tool Entry

Use the bundled script from this Codex project skill:

```bash
python .codex/skills/mathkg-process/scripts/mathkg_agent_tool.py <subcommand> <input.md> [pipeline options]
```

Common subcommands:

- `scan-cache`: return `_stage_cache` file facts and basic counts.
- `load-agent-state`: read memory, quality facts, decisions, review packets, and report.
- `next-action`: return the single unconsumed transition implied by current artifacts and recorded decisions.
- `validate-stage --stage <stage>`: return fact-only structural checks.
- `build-review-packet --stage extract_statements`: build source/output evidence chunks for semantic review.
- `run-stage --stage <stage>`: execute one existing backend stage.
- `rerun-failed-tasks --stage <stage>`: rerun only unresolved task keys for a supported partially failed stage.
- `rerun-extract-statements --repair-intent <json-or-path>`: generate a sidecar repair candidate.
- `build-candidate-review-packet --repair-id <id>`: build candidate evidence for main-agent review.
- `apply-repair --repair-id <id> --decision <json-or-path>`: apply only an approved candidate.
- `write-agent-decision --decision '<json>'`: append your decision record.
- `write-run-report`: write the final run report.

`extract_statements` has two engine modes:

- Default: `--llm-engine api`
- Optional, only when the user requests it or you explicitly decide it is needed: `--llm-engine claude_cli`

If the user does not mention Claude CLI, use the default API mode. The Claude
CLI engine batches original source blocks and does not split a single block by
character length.

See `references/commands.md` for complete command examples.

## Main Loop

Follow the fixed stage order:

```text
correct_text
segment_blocks
extract_statements
clean_nodes
split_nodes
generate_titles
math_disambiguation
extract_logic_tuples
analysis
repair
extract_references
repair_lite
build_relations
finalize_output
```

`compile_logic_form` and `normalize_predicates` are an experimental side path,
not part of the default loop. Only when the run was explicitly started with
`--experimental-logic-ir`, insert them after `repair_lite` and before
`build_relations`; keep that flag on every subsequent tool command for the run.

One skill invocation owns one orchestration loop. Never invoke
`Skill("mathkg-process")`, `/mathkg-process`, or this skill from inside itself.
Never use TaskCreate, TaskUpdate, or a task list as the pipeline state machine.

1. Call `scan-cache`, then `load-agent-state`.
2. Make any needed cache or semantic judgment.
3. Call `next-action`.
4. If `action_kind` is `execute_command`, execute exactly the returned `suggested_command`.
5. If `action_kind` is `ask_user`, ask the user the returned confirmation question.
6. If `action_kind` is `agent_decision`, read the cited evidence once and immediately write the required decision.
7. Do not emit progress text that lacks a concrete stage/action, such as "continue the state machine" or "directly advance the flow".
8. After a decision or artifact is created, call `next-action` once again.
9. Continue only when `state_fingerprint` changed.
10. If `repeat_guard.triggered` is true, stop and report the pending action.
11. Stop after an intentional pause or after `write-run-report`.

`next-action` does not judge semantic quality. It consumes recorded decisions
and artifacts. You remain responsible for cache trust, semantic review,
candidate approval, and final readiness.

Read `references/state-machine.md` before handling any non-trivial
`next-action` state. Do not use todo updates as pipeline progress; `next-action`
is the state machine.

## Required Review Gate

After `extract_statements` is present or has just run, structural validation is
only the safety floor. You must run the full semantic review:

1. `validate-stage --stage extract_statements`
2. `build-review-packet --stage extract_statements`
3. Read every manifest chunk. Do not sample.
4. Compare each source block against its extracted nodes.
5. Write `semantic_review_extract_statements`.

Blocking `extract_statements` findings do not end the run. Present the blocking
findings and proposed repair intent to the user. Only after explicit user
confirmation may you write `repair_intent` with `user_confirmed: true`.

While waiting for confirmation, do not call `rerun-extract-statements`, do not
invent confirmation, and do not write the final report.

Read `references/extract-statements-review.md` for semantic criteria and
`references/repair-loop.md` before writing repair or candidate decisions.

## Stage Responsibilities

- `correct_text`: corrected text exists, parses, and LaTeX commands, environments, and math delimiters are not obviously damaged.
- `segment_blocks`: `problem_dict` contains meaningful source spans, not widespread empty fragments.
- `extract_statements`: structural validation plus the required full semantic review.
- `clean_nodes`: use the backend MultiProcessor cleaning stage to quarantine nodes with no standalone mathematical meaning before `split_nodes`.
- `split_nodes`: preserve parent-visible identity and theorem assumptions.
- `generate_titles`: ensure title coverage and useful anchors, not copied full statements.
- `math_disambiguation`: preserve counts and structure while resolving notation ambiguity.
- `extract_logic_tuples`: ensure final `node_dict` is populated and structured fields are plausible.
- If any supported stage reports unresolved task keys, run `rerun-failed-tasks --stage <stage>` for that stage; never trust older downstream cache as proof that the partial run succeeded.
- `analysis` and `repair`: only when enabled; ensure repair does not corrupt identity fields.
- `extract_references`: distinguish localized unresolved references from systematic failure.
- `repair_lite`: ensure deterministic repair reports correspond to current references.
- Experimental `compile_logic_form`: ensure logic outputs align and AST render errors are localized.
- Experimental `normalize_predicates`: ensure registry and rewrite map are coherent.
- `build_relations`: ensure edges point to valid current node ids and empty graphs are explainable.
- `finalize_output`: ensure final node and edge outputs parse and match late-stage cache facts.

## Final Report

Before finishing, call `write-run-report`. In the user-facing answer, summarize
reused stages, executed stages, `extract_statements` semantic judgment, repair
activity, manual-review items, and whether final outputs are ready downstream.
