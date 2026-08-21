# AGENTS.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Reuse Project Defaults

**Search first. Use the project's high-level entry points instead of rebuilding infrastructure.**

Before implementing a common capability:
- Search for the concept, public symbol, and existing call sites with `rg`.
- Read at least one current caller or test so the existing contract is understood.
- Reuse or extend the canonical module when it fits.
- Add a new implementation only when the existing tool cannot satisfy the requirement, and state why.

### Backend Default Tools

| Need | Default tool | Usage rule |
| --- | --- | --- |
| Batched or stage-level LLM work | `pipeline.common.llm_task.run_multiprocess_task` (backed by `JoinAgent.Multi_Process.MultiProcessor`) | Use the wrapper so API/Claude engine selection, parsing, validation, concurrency, checkpoints, and retries stay consistent. |
| One genuinely synchronous LLM request | `PipelineContext.llm` | Call `context.llm.ask(...)`; do not create another `SimpleLLM` client inside pipeline code. |
| LLM output parsing | `PipelineContext.parser` / `JoinAgent.LLMParser` | Reuse `parse_list`, `parse_dict`, `parse_pads`, or `parse_code` instead of writing ad hoc parsers. |
| Configuration and runtime dependencies | `pipeline.config` / `pipeline.context.PipelineContext` | Reuse URL normalization, environment resolution, LLM/parser/divider construction, output directories, and checkpoint settings. |
| Checkpoints, failure reports, and task reruns | `pipeline.common.stage_recovery` | Use the recoverable-task and unresolved-task helpers instead of inventing retry state or report formats. |
| JSON and stage artifacts | `pipeline.common.io` | Reuse directory creation, JSON read/write, stage dumps, and default analysis paths. |
| Node access and normalization | `pipeline.common.node` | Reuse node getters, type/text normalization, subnode/match-unit helpers, and `global_id` generation. |
| TeX parsing and macro handling | `pipeline.common.tex` / `backend.tex_macros` | Reuse source-model, statement extraction, stage-output, and macro extraction/merge helpers. |
| Formalization checks | `pipeline.common.formalization_guards` | Reuse statement skeleton, concept/binding extraction, context payload, and risk checks. |
| Pipeline execution | Existing `pipeline.stages` modules and `pipeline.orchestrator` | Run or extend the existing stage and fixed-stage plan; do not duplicate stage control flow. |
| MathKG run, resume, audit, or repair orchestration | `backend/.codex/skills/mathkg-process` | Use the skill's bundled commands and state machine instead of editing canonical cache or recreating orchestration. |
| OCR cleanup and Neo4j access | `backend.tools.cleaner.clean_mineru_output` / `backend.integrations.neo4j_handler.Neo4jHandler` | Reuse the project integration boundary rather than opening a second cleanup or database path. |

For any collection of LLM tasks, do not introduce a new `ThreadPoolExecutor`,
`asyncio.gather`, thread pool, retry loop, or checkpoint implementation. Use
`run_multiprocess_task`. Direct `context.llm.ask(...)` is reserved for a truly
single interactive request.

Keep stable logic shared by multiple stages in `pipeline/common`. Keep
stage-specific transformations in the corresponding `pipeline/stages/<stage>`
directory.

## 4. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 5. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
