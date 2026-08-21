from contextlib import contextmanager
from contextvars import ContextVar

from JoinAgent import MultiProcessor

from .claude_cli_engine import ClaudeCliEngine


_ACTIVE_RELOAD = ContextVar("pipeline_active_reload", default=False)


@contextmanager
def active_reload_scope(enabled):
    token = _ACTIVE_RELOAD.set(bool(enabled))
    try:
        yield
    finally:
        _ACTIVE_RELOAD.reset(token)


def run_multiprocess_task(
    *,
    llm,
    parse_method,
    data_template,
    prompt_template,
    correction_template,
    validator,
    index_dict,
    num_threads,
    checkpoint,
    back_up_llm=None,
    checkpoint_dir=None,
    active_reload=False,
    active_transform=False,
    engine="api",
    stage_name=None,
    output_dir=None,
    claude_command="claude",
    claude_model=None,
    claude_agent=None,
    claude_batch_size=8,
    claude_timeout_seconds=900,
    claude_max_retries=1,
):
    active_reload = bool(active_reload or _ACTIVE_RELOAD.get())
    if engine == "claude_cli":
        if not stage_name:
            raise ValueError("stage_name is required when engine='claude_cli'")
        if not output_dir:
            raise ValueError("output_dir is required when engine='claude_cli'")
        claude_engine = ClaudeCliEngine(
            stage_name=stage_name,
            output_dir=output_dir,
            command=claude_command,
            model=claude_model,
            agent=claude_agent,
            batch_size=claude_batch_size,
            timeout_seconds=claude_timeout_seconds,
            max_retries=claude_max_retries,
        )
        return claude_engine.run_tasks(
            parse_method=parse_method,
            data_template=data_template,
            prompt_template=prompt_template,
            correction_template=correction_template,
            validator=validator,
            index_dict=index_dict,
            checkpoint=checkpoint,
            checkpoint_dir=checkpoint_dir,
            active_reload=active_reload,
            active_transform=active_transform,
        )
    if engine != "api":
        raise ValueError(f"Unknown LLM engine: {engine}")

    processor = MultiProcessor(
        llm=llm,
        parse_method=parse_method,
        data_template=data_template,
        prompt_template=prompt_template,
        correction_template=correction_template,
        validator=validator,
        back_up_llm=back_up_llm,
        checkpoint_dir=checkpoint_dir,
    )
    return processor.multitask_perform(
        index_dict,
        num_threads=num_threads,
        checkpoint=checkpoint,
        Active_Reload=active_reload,
        Active_Transform=active_transform,
        checkpoint_dir=checkpoint_dir,
    )

