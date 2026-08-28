import os
from dataclasses import dataclass, field

from JoinAgent import LLMParser, SimpleLLM, TextDivider
from .config import load_env_file, resolve_embedding_config, resolve_llm_config


DEFAULT_NUM_THREADS = 16
DEFAULT_CHECKPOINT = 500
DEFAULT_EMBEDDING_MODEL = "qwen3-embedding-8b"


@dataclass
class PipelineContext:
    file_path: str
    output_node_path: str | None = None
    output_edge_path: str | None = None
    output_natural_node_path: str | None = None
    api_url: str | None = None
    model_name: str | None = None
    api_key: str | None = None
    embedding_api_url: str | None = None
    embedding_api_key: str | None = None
    num_threads: int = DEFAULT_NUM_THREADS
    checkpoint: int = DEFAULT_CHECKPOINT
    enable_analysis: bool = False
    enable_math_disambiguation: bool = True
    ambiguity_table: object | None = None
    embedding_model_name: str = DEFAULT_EMBEDDING_MODEL
    relation_retrieval_mode: str = "hybrid_strict"
    llm_engine: str = "api"
    claude_command: str | list[str] = "claude"
    claude_model: str | None = None
    claude_agent: str | None = None
    claude_batch_size: int = 8
    claude_timeout_seconds: int = 900
    claude_max_retries: int = 1
    source_format: str = "auto"
    source_origin: str = "markdown"
    execution_mode: str = "pipeline"
    cache_policy: str = "legacy"
    llm: SimpleLLM | None = None
    parser: LLMParser | None = None
    divider: TextDivider | None = None
    output_dir: str = field(init=False)
    stage_cache_dir: str = field(init=False)
    stage_work_dir: str | None = field(init=False)
    checkpoint_root: str = field(init=False)
    current_stage_key: str | None = field(init=False, default=None)
    resume_task_checkpoints: bool = field(init=False, default=False)

    def __post_init__(self):
        load_env_file()
        resolved = resolve_llm_config(self.api_url, self.model_name, self.api_key)
        self.api_url = resolved.api_url
        self.model_name = resolved.model_name
        self.api_key = resolved.api_key
        embedding = resolve_embedding_config(self.embedding_api_url, self.embedding_api_key)
        self.embedding_api_url = embedding.api_url
        self.embedding_api_key = embedding.api_key
        self.embedding_model_name = self._resolve_embedding_model(self.embedding_model_name)
        self.relation_retrieval_mode = self._resolve_relation_retrieval_mode(self.relation_retrieval_mode)
        self.file_path = os.path.abspath(self.file_path)
        self.source_format = self._resolve_source_format(self.source_format)
        self.source_origin = self._resolve_source_origin(self.source_origin)
        self.execution_mode = self._resolve_execution_mode(self.execution_mode)
        self.cache_policy = self._resolve_cache_policy(self.cache_policy)

        output_base_dir = None
        for candidate in (self.output_node_path, self.output_edge_path, self.output_natural_node_path):
            if candidate:
                output_base_dir = os.path.dirname(os.path.abspath(candidate))
                break
        if not output_base_dir:
            output_base_dir = os.path.dirname(self.file_path)

        self.stage_cache_dir = os.path.join(output_base_dir, "_stage_cache")
        os.makedirs(self.stage_cache_dir, exist_ok=True)
        if self.cache_policy == "minimal":
            self.stage_work_dir = os.path.join(output_base_dir, "_stage_work")
            self.output_dir = self.stage_work_dir
            os.makedirs(self.output_dir, exist_ok=True)
            self.checkpoint_root = os.path.join(self.stage_cache_dir, "checkpoints")
        else:
            self.stage_work_dir = None
            self.output_dir = self.stage_cache_dir
            self.checkpoint_root = os.path.join(self.output_dir, "checkpoint")

        if self.llm is None:
            self.llm = self._build_llm()
        if self.parser is None:
            self.parser = LLMParser()
        if self.divider is None:
            self.divider = TextDivider(threshold=4096, overlap=0)

    def _resolve_source_format(self, value):
        value = (value or "auto").strip().lower()
        if value not in {"auto", "markdown", "md", "tex"}:
            raise ValueError("source_format only supports auto / markdown / tex")
        if value == "md":
            value = "markdown"
        if value != "auto":
            return value
        ext = os.path.splitext(self.file_path)[1].lower()
        if ext == ".tex":
            return "tex"
        return "markdown"

    def _resolve_source_origin(self, value):
        value = (value or "markdown").strip().lower()
        if value not in {"markdown", "ocr"}:
            raise ValueError("source_origin only supports markdown / ocr")
        return value

    def _resolve_execution_mode(self, value):
        value = (value or "pipeline").strip().lower()
        if value not in {"pipeline", "agent"}:
            raise ValueError("execution_mode only supports pipeline / agent")
        return value

    def _resolve_cache_policy(self, value):
        value = (value or "legacy").strip().lower()
        if value not in {"legacy", "minimal"}:
            raise ValueError("cache_policy only supports legacy / minimal")
        return value

    def _resolve_embedding_model(self, value):
        env_value = os.getenv("PDFPIPELINE_EMBEDDING_MODEL") or os.getenv("EMBEDDING_MODEL")
        if value == DEFAULT_EMBEDDING_MODEL and env_value:
            return env_value.strip()
        return (value or DEFAULT_EMBEDDING_MODEL).strip()

    def _resolve_relation_retrieval_mode(self, value):
        value = (value or "hybrid_strict").strip().lower()
        if value not in {"hybrid_strict", "sparse_preview"}:
            raise ValueError("relation_retrieval_mode only supports hybrid_strict / sparse_preview")
        return value

    def _build_llm(self):
        if self.api_url and self.model_name and self.api_key:
            print(f"Using custom API: {self.api_url}")
            print(f"Model: {self.model_name}")
            return SimpleLLM(
                model=self.model_name,
                api_url=self.api_url,
                api_key=self.api_key,
            )

        print("Using default API configuration")
        return SimpleLLM()
