import copy
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.stages.math_disambiguation.ambiguity_table import get_ambiguity_table
from pipeline.stages.math_disambiguation.stage import (
    build_disambiguation_tasks,
    restore_definition_axiom_dict,
    restore_structured_input_dict,
    scan_node_dict_for_ambiguity,
)

TMP_ROOT = Path(__file__).resolve().parent / '_tmp_math_disambiguation_task_payload'


def _make_temp_dir():
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    temp_dir = TMP_ROOT / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def _build_definition_axiom_dict():
    return {
        1: {
            'node_type': 'definition',
            'title': {'chinese': '包含关系', 'english': 'Containment'},
            'content': r'A \subset B',
            'proof': '',
            'label': 'Def 1',
        }
    }


def _build_structured_input_dict():
    return {
        2: {
            'pos1': {
                'node_type': 'property',
                'title': {'chinese': '子群包含', 'english': 'Subgroup Containment'},
                'content': r'x \subset H',
                'proof': '',
                'label': 'Prop 1',
            },
            '_orig_key': 2,
        }
    }


def test_build_disambiguation_tasks_embeds_hits_without_mutating_source():
    ambiguity_table = get_ambiguity_table()
    ambiguous_node_dict = scan_node_dict_for_ambiguity(
        _build_definition_axiom_dict(),
        ambiguity_table=ambiguity_table,
        container_name='definition_axiom_dict',
        is_wrapped=False,
    )

    original_node = copy.deepcopy(ambiguous_node_dict[1]['node'])
    original_hits = copy.deepcopy(ambiguous_node_dict[1]['ambiguity_hits'])

    task_dict = build_disambiguation_tasks(ambiguous_node_dict)
    task = task_dict[1]

    assert task['source_key'] == '1'
    assert task['pos1']['content'] == original_node['content']
    assert task['pos1']['ambiguity_hits'] == original_hits
    assert ambiguous_node_dict[1]['node'] == original_node
    assert 'ambiguity_hits' not in ambiguous_node_dict[1]['node']


def test_restore_functions_strip_ambiguity_hits_from_llm_output():
    ambiguity_table = get_ambiguity_table()

    definition_axiom_dict = _build_definition_axiom_dict()
    definition_ambiguous_node_dict = scan_node_dict_for_ambiguity(
        definition_axiom_dict,
        ambiguity_table=ambiguity_table,
        container_name='definition_axiom_dict',
        is_wrapped=False,
    )
    definition_llm_result = {
        1: {
            **definition_axiom_dict[1],
            'content': 'Subset(A,B)',
            'original_content': definition_axiom_dict[1]['content'],
            'ambiguity_hits': copy.deepcopy(definition_ambiguous_node_dict[1]['ambiguity_hits']),
        }
    }
    restored_definition = restore_definition_axiom_dict(
        definition_axiom_dict,
        definition_ambiguous_node_dict,
        definition_llm_result,
    )

    assert restored_definition[1]['content'] == definition_axiom_dict[1]['content']
    assert restored_definition[1]['disambiguated_content'] == 'Subset(A,B)'
    assert 'ambiguity_hits' not in restored_definition[1]
    assert 'ambiguity_hits' in definition_llm_result[1]

    structured_input_dict = _build_structured_input_dict()
    structured_ambiguous_node_dict = scan_node_dict_for_ambiguity(
        structured_input_dict,
        ambiguity_table=ambiguity_table,
        container_name='structured_input_dict',
        is_wrapped=True,
    )
    structured_llm_result = {
        2: {
            **structured_input_dict[2]['pos1'],
            'content': 'Subset(x,H)',
            'original_content': structured_input_dict[2]['pos1']['content'],
            'ambiguity_hits': copy.deepcopy(structured_ambiguous_node_dict[2]['ambiguity_hits']),
        }
    }
    restored_structured = restore_structured_input_dict(
        structured_input_dict,
        structured_ambiguous_node_dict,
        structured_llm_result,
    )

    assert restored_structured[2]['pos1']['content'] == structured_input_dict[2]['pos1']['content']
    assert restored_structured[2]['pos1']['disambiguated_content'] == 'Subset(x,H)'
    assert 'ambiguity_hits' not in restored_structured[2]['pos1']
    assert 'ambiguity_hits' in structured_llm_result[2]


def test_scan_preview_script_includes_ambiguity_hits_inside_pos1():
    tmp_path = _make_temp_dir()
    try:
        stage_cache_dir = tmp_path / '_stage_cache'
        stage_cache_dir.mkdir(parents=True, exist_ok=True)

        (stage_cache_dir / 'definition_axiom_dict.json').write_text(
            json.dumps(_build_definition_axiom_dict(), ensure_ascii=False, indent=4),
            encoding='utf-8',
        )
        (stage_cache_dir / 'structured_input_dict.json').write_text(
            json.dumps(_build_structured_input_dict(), ensure_ascii=False, indent=4),
            encoding='utf-8',
        )

        subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / 'scripts' / 'test_math_disambiguation_scan.py'),
                '--stage-cache-dir',
                str(stage_cache_dir),
            ],
            check=True,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )

        preview_path = stage_cache_dir / 'math_disambiguation_scan_preview.json'
        payload = json.loads(preview_path.read_text(encoding='utf-8'))

        definition_task = payload['definition_llm_input_dict']['1']
        structured_task = payload['structured_llm_input_dict']['2']

        assert definition_task['source_key'] == '1'
        assert structured_task['source_key'] == '2'
        assert 'ambiguity_hits' in definition_task['pos1']
        assert 'ambiguity_hits' in structured_task['pos1']
        assert definition_task['pos1']['ambiguity_hits'][0]['symbol'] == r'\subset'
        assert structured_task['pos1']['ambiguity_hits'][0]['symbol'] == r'\subset'
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')

    test_build_disambiguation_tasks_embeds_hits_without_mutating_source()
    test_restore_functions_strip_ambiguity_hits_from_llm_output()
    test_scan_preview_script_includes_ambiguity_hits_inside_pos1()
    print('math disambiguation task payload tests passed')
