import os
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.context import DEFAULT_NUM_THREADS, PipelineContext
from pipeline.config import resolve_embedding_config


def test_stage_cache_follows_final_output_dir():
    run_dir = tempfile.mkdtemp(prefix=f'pipeline_context_{uuid.uuid4().hex}_')

    input_path = os.path.join(run_dir, 'input.md')
    node_path = os.path.join(run_dir, 'final', 'sample_node.json')
    edge_path = os.path.join(run_dir, 'final', 'sample_edge.json')
    with open(input_path, 'w', encoding='utf-8') as f:
        f.write('# demo')

    context = PipelineContext(
        file_path=input_path,
        output_node_path=node_path,
        output_edge_path=edge_path,
        llm=object(),
        parser=object(),
        divider=object(),
    )

    expected_output_dir = os.path.join(run_dir, 'final', '_stage_cache')
    assert context.output_dir == expected_output_dir
    assert context.checkpoint_root == os.path.join(expected_output_dir, 'checkpoint')
    assert os.path.isdir(context.output_dir)


def test_pipeline_context_auto_detects_tex_source_format():
    run_dir = tempfile.mkdtemp(prefix=f'pipeline_context_{uuid.uuid4().hex}_')

    input_path = os.path.join(run_dir, 'input.tex')
    with open(input_path, 'w', encoding='utf-8') as f:
        f.write(r'\begin{theorem}\label{thm:a}A.\end{theorem}')

    context = PipelineContext(
        file_path=input_path,
        llm=object(),
        parser=object(),
        divider=object(),
    )

    assert context.source_format == 'tex'


def test_pipeline_context_defaults_to_pipeline_execution_mode():
    run_dir = tempfile.mkdtemp(prefix=f'pipeline_context_{uuid.uuid4().hex}_')

    input_path = os.path.join(run_dir, 'input.md')
    with open(input_path, 'w', encoding='utf-8') as f:
        f.write('# demo')

    context = PipelineContext(
        file_path=input_path,
        llm=object(),
        parser=object(),
        divider=object(),
    )

    assert context.execution_mode == 'pipeline'
    assert DEFAULT_NUM_THREADS == 16
    assert context.num_threads == 16


def test_pipeline_context_accepts_agent_execution_mode():
    run_dir = tempfile.mkdtemp(prefix=f'pipeline_context_{uuid.uuid4().hex}_')

    input_path = os.path.join(run_dir, 'input.md')
    with open(input_path, 'w', encoding='utf-8') as f:
        f.write('# demo')

    context = PipelineContext(
        file_path=input_path,
        execution_mode='agent',
        llm=object(),
        parser=object(),
        divider=object(),
    )

    assert context.execution_mode == 'agent'


def test_pipeline_context_uses_independent_embedding_credentials():
    run_dir = tempfile.mkdtemp(prefix=f'pipeline_context_{uuid.uuid4().hex}_')
    input_path = os.path.join(run_dir, 'input.md')
    with open(input_path, 'w', encoding='utf-8') as f:
        f.write('# demo')

    env = {
        'PDFPIPELINE_API_URL': 'https://chat.example.test/v1',
        'PDFPIPELINE_API_KEY': 'chat-key',
        'EMBEDDING_API_URL': 'https://embedding.example.test/v1/',
        'EMBEDDING_API_KEY': 'embedding-key',
    }
    with patch.dict(os.environ, env):
        context = PipelineContext(
            file_path=input_path,
            llm=object(),
            parser=object(),
            divider=object(),
        )

    assert context.api_key == 'chat-key'
    assert context.embedding_api_url == 'https://embedding.example.test/v1'
    assert context.embedding_api_key == 'embedding-key'
    assert context.embedding_api_key != context.api_key


def test_embedding_config_accepts_lowercase_env_names_without_chat_fallback():
    env = {
        'PDFPIPELINE_API_URL': 'https://chat.example.test/v1',
        'PDFPIPELINE_API_KEY': 'chat-key',
        'embedding_api_url': 'https://lowercase-embedding.example.test/v1/',
        'embedding_api_key': 'lowercase-embedding-key',
    }
    with patch.dict(os.environ, env, clear=True):
        embedding = resolve_embedding_config()

    assert embedding.api_url == 'https://lowercase-embedding.example.test/v1'
    assert embedding.api_key == 'lowercase-embedding-key'
    assert embedding.api_key != env['PDFPIPELINE_API_KEY']


if __name__ == '__main__':
    test_stage_cache_follows_final_output_dir()
    test_pipeline_context_auto_detects_tex_source_format()
    test_pipeline_context_defaults_to_pipeline_execution_mode()
    test_pipeline_context_accepts_agent_execution_mode()
    test_pipeline_context_uses_independent_embedding_credentials()
    test_embedding_config_accepts_lowercase_env_names_without_chat_fallback()
    print('pipeline context tests passed')
