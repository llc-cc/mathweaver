# MathKG Command Cookbook

Use these examples from the repository root. Replace `<input.md>`, `<stage>`,
`<repair_id>`, and JSON values with the current run values.

Stage tools clear proxy environment variables internally while they run. Do not
prepend `$env:HTTP_PROXY=''` or similar proxy-clearing shell code.

## Claude Code Primary Tool

```bash
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py <subcommand> <input.md> [options]
```

Codex equivalent:

```bash
python .codex/skills/mathkg-process/scripts/mathkg_agent_tool.py <subcommand> <input.md> [options]
```

## Start Or Resume

```bash
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py scan-cache <input.md>
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py load-agent-state <input.md>
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py next-action <input.md>
```

## Run A Stage

Default stage execution:

```bash
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py run-stage <input.md> --stage <stage>
```

Run `extract_statements` with default API mode:

```bash
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py run-stage <input.md> --stage extract_statements --llm-engine api
```

Run `extract_statements` with Claude CLI when requested:

```bash
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py run-stage <input.md> --stage extract_statements --llm-engine claude_cli --claude-command claude --claude-model deepseek-v4-flash
```

Rerun only unresolved task keys for the stage reported by `next-action`:

```bash
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py rerun-failed-tasks <input.md> --stage <stage>
```

## Validate And Review extract_statements

```bash
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py validate-stage <input.md> --stage extract_statements
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py build-review-packet <input.md> --stage extract_statements
```

After reading every manifest chunk, write a semantic review decision from a JSON
file:

```bash
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py write-agent-decision <input.md> --decision semantic_review_decision.json
```

## Repair Candidate

After user confirmation, write a repair intent from a JSON file:

```bash
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py write-agent-decision <input.md> --decision repair_intent_decision.json
```

Generate a sidecar candidate with API mode:

```bash
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py rerun-extract-statements <input.md> --repair-intent repair_intent.json --llm-engine api
```

Generate a sidecar candidate with Claude CLI when requested:

```bash
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py rerun-extract-statements <input.md> --repair-intent repair_intent.json --llm-engine claude_cli --claude-command claude --claude-model deepseek-v4-flash
```

Build candidate review evidence:

```bash
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py build-candidate-review-packet <input.md> --repair-id <repair_id>
```

## Apply And Recheck

```bash
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py apply-repair <input.md> --repair-id <repair_id> --decision apply_decision.json
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py validate-stage <input.md> --stage extract_statements
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py build-review-packet <input.md> --stage extract_statements
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py next-action <input.md>
```

## Final Report

```bash
python .claude/skills/mathkg-process/scripts/mathkg_agent_tool.py write-run-report <input.md>
```
