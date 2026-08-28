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
from pipeline.stages.math_disambiguation.stage import compile_ambiguity_symbols, find_ambiguity_hits

TMP_ROOT = Path(__file__).resolve().parent / '_tmp_math_disambiguation_bar_hits'


def _make_temp_dir():
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    temp_dir = TMP_ROOT / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def _hit_by_symbol(hits, symbol):
    for hit in hits:
        if hit['symbol'] == symbol:
            return hit
    return None


def test_single_bar_pair_hits_whole_expression():
    compiled_symbols = compile_ambiguity_symbols(get_ambiguity_table())
    hits = find_ambiguity_hits(r'If $|G|$ is odd', compiled_symbols)

    paired_hit = _hit_by_symbol(hits, '|...|')
    assert paired_hit is not None
    assert paired_hit['match_count'] == 1
    assert paired_hit['matches'][0]['text'] == '|G|'
    assert _hit_by_symbol(hits, '|') is None


def test_double_bar_pair_hits_whole_expression():
    compiled_symbols = compile_ambiguity_symbols(get_ambiguity_table())
    hits = find_ambiguity_hits(r'||x|| = 1', compiled_symbols)

    paired_hit = _hit_by_symbol(hits, '||...||')
    assert paired_hit is not None
    assert paired_hit['match_count'] == 1
    assert paired_hit['matches'][0]['text'] == '||x||'
    assert _hit_by_symbol(hits, '|...|') is None


def test_bare_bar_still_hits_as_infix_divides():
    compiled_symbols = compile_ambiguity_symbols(get_ambiguity_table())
    hits = find_ambiguity_hits('a | b', compiled_symbols)

    bare_hit = _hit_by_symbol(hits, '|')
    assert bare_hit is not None
    assert bare_hit['match_count'] == 1
    assert bare_hit['matches'][0]['text'] == '|'
    assert _hit_by_symbol(hits, '|...|') is None


def test_spaced_single_bar_pair_hits_whole_expression():
    compiled_symbols = compile_ambiguity_symbols(get_ambiguity_table())
    hits = find_ambiguity_hits(r'$| T : H |$', compiled_symbols)

    paired_hit = _hit_by_symbol(hits, '|...|')
    assert paired_hit is not None
    assert paired_hit['match_count'] == 1
    assert paired_hit['matches'][0]['text'] == '| T : H |'


def test_bar_pairs_do_not_break_other_symbols():
    compiled_symbols = compile_ambiguity_symbols(get_ambiguity_table())
    hits = find_ambiguity_hits(r'|x| + ||y|| + A \subset B', compiled_symbols)

    single_hit = _hit_by_symbol(hits, '|...|')
    double_hit = _hit_by_symbol(hits, '||...||')
    subset_hit = _hit_by_symbol(hits, r'\subset')

    assert single_hit is not None
    assert double_hit is not None
    assert subset_hit is not None
    assert single_hit['matches'][0]['text'] == '|x|'
    assert double_hit['matches'][0]['text'] == '||y||'
    assert subset_hit['matches'][0]['text'] == r'\subset'


def test_sample_scan_preview_uses_paired_bar_hit():
    sample_stage_cache_dir = PROJECT_ROOT / 'test_output' / 'PDF合并_ch1' / '_stage_cache'
    if not sample_stage_cache_dir.exists():
        return

    tmp_path = _make_temp_dir()
    try:
        preview_path = tmp_path / 'math_disambiguation_scan_preview.json'
        subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / 'scripts' / 'test_math_disambiguation_scan.py'),
                '--stage-cache-dir',
                str(sample_stage_cache_dir),
                '--output',
                str(preview_path),
            ],
            check=True,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )

        payload = json.loads(preview_path.read_text(encoding='utf-8'))
        structured_ambiguous_node_dict = payload['structured_ambiguous_node_dict']

        first_hit = structured_ambiguous_node_dict['0']['ambiguity_hits'][0]
        assert first_hit['symbol'] == '|...|'
        assert first_hit['match_count'] == 1
        assert first_hit['matches'][0]['text'] == '|G|'
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')

    test_single_bar_pair_hits_whole_expression()
    test_double_bar_pair_hits_whole_expression()
    test_bare_bar_still_hits_as_infix_divides()
    test_spaced_single_bar_pair_hits_whole_expression()
    test_bar_pairs_do_not_break_other_symbols()
    test_sample_scan_preview_uses_paired_bar_hit()
    print('math disambiguation bar hit tests passed')
