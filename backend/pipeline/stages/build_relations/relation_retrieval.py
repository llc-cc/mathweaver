"""Auditable multi-channel candidate retrieval for relation extraction.

Retrieval is deliberately separated from relation adjudication: a candidate score
can only decide which node pairs are sent to the existing relation LLM.  It never
creates a knowledge-graph edge by itself.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ...common.node import (
    assemble_statement_text,
    get_node_content,
    get_node_field_texts,
    get_node_formal_content,
    get_node_label,
    get_node_node_type,
    get_node_proof,
    get_node_title,
    is_definition_node_type,
    is_relation_statement_node_type,
)


EMBEDDING_CACHE_SCHEMA_VERSION = 1
RRF_K = 60
LOGIC_PROMPT_CONTRACT_VERSION = 2

_TOKEN_PATTERN = re.compile(
    r"\\[A-Za-z]+|[A-Za-z][A-Za-z0-9_]*|[0-9]+(?:\.[0-9]+)*|[\u4e00-\u9fff]+"
)
_LATEX_OR_IDENTIFIER_PATTERN = re.compile(r"^(?:\\[A-Za-z]+|[A-Za-z][A-Za-z0-9_]{1,})$")
_GENERIC_TOKENS = {
    "and",
    "are",
    "for",
    "from",
    "have",
    "into",
    "let",
    "mathbb",
    "mathbf",
    "mathrm",
    "operatorname",
    "proof",
    "that",
    "the",
    "then",
    "this",
    "with",
    "一个",
    "上述",
    "于是",
    "任意",
    "其中",
    "则有",
    "可以",
    "定义",
    "定理",
    "引理",
    "命题",
    "推论",
    "证明",
}
_GENERIC_PREDICATES = {
    "",
    "UNKNOWN",
    "UNKNOWN_PREDICATE",
    "P_UNKNOWN",
    "ENTITY",
}


@dataclass(frozen=True)
class RelationRetrievalConfig:
    mode: str = "hybrid_strict"
    implicit_direction: str = "backward_only"
    window_k: int = 8
    logic_channel_k: int = 20
    definition_channel_k: int = 15
    graph_expand_k: int = 10
    pre_rerank_k: int = 80
    definition_pre_rerank_k: int = 40
    logic_final_k: int = 20
    definition_final_k: int = 10
    rerank_batch_size: int = 20
    rrf_k: int = RRF_K
    per_channel_protected_k: int = 2

    def validate(self) -> "RelationRetrievalConfig":
        if self.mode not in {"hybrid_strict", "sparse_preview"}:
            raise ValueError("relation retrieval mode only supports hybrid_strict / sparse_preview")
        if self.implicit_direction != "backward_only":
            raise ValueError("implicit_direction currently only supports backward_only")
        for name, value in asdict(self).items():
            if name in {"mode", "implicit_direction"}:
                continue
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.logic_final_k + self.definition_final_k > 30:
            raise ValueError("final relation candidate budget must not exceed 30")
        return self


@dataclass
class RelationCandidate:
    candidate_id: str
    dependent_global_id: str
    support_global_id: str
    dependent_index: int
    support_index: int
    relation_kind: str
    retrieval_channels: list[str] = field(default_factory=list)
    channel_ranks: dict[str, int] = field(default_factory=dict)
    channel_scores: dict[str, float] = field(default_factory=dict)
    matched_predicates: list[str] = field(default_factory=list)
    matched_symbols: list[str] = field(default_factory=list)
    matched_aliases: list[str] = field(default_factory=list)
    rrf_score: float = 0.0
    rerank_score: float | None = None
    rerank_rank: int | None = None
    protected: bool = False
    selected: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class EmbeddingRetrievalError(RuntimeError):
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report or {}


def _normalized_text(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _flatten_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        preferred = []
        for key in ("text_normalized", "text", "original_form", "formal_statement_core", "chinese", "english"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                preferred.append(item)
        if preferred:
            return " ".join(preferred)
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(item) for item in value)
    return ""


def _field_text(node, field_name):
    if field_name in {"conditions", "conclusions"}:
        return " ".join(get_node_field_texts(node, field_name))
    return _flatten_text(node.get(field_name))


def _source_original_form(node):
    if not isinstance(node, dict):
        return ""
    envelope = node.get("_source_envelope")
    if isinstance(envelope, dict):
        value = envelope.get("source_original_form") or envelope.get("source_text")
        if isinstance(value, str) and value.strip():
            return value
    value = node.get("source_original_form")
    if isinstance(value, str) and value.strip():
        return value
    for field_name in ("content", "remark"):
        value = node.get(field_name)
        if isinstance(value, dict):
            original = value.get("original_form")
            if isinstance(original, str) and original.strip():
                return original
    return get_node_content(node)


def _node_text_views(node, pair_mode="structured"):
    title = get_node_title(node)
    label = get_node_label(node)
    proof = get_node_proof(node)
    source_original_form = _source_original_form(node)
    derivation_context = _flatten_text(node.get("derivation_context"))
    conditions = _field_text(node, "conditions")
    conclusions = _field_text(node, "conclusions")
    subject = _field_text(node, "subject")
    context = _field_text(node, "context")
    content = get_node_content(node)
    formal = get_node_formal_content(node)
    statement = assemble_statement_text(node) if pair_mode == "structured" else content
    logic_query = " ".join(
        part
        for part in (
            conditions,
            conclusions,
            proof,
            derivation_context,
            source_original_form,
            subject,
            context,
            statement,
        )
        if part
    )
    logic_support = " ".join(part for part in (conclusions, title, formal, statement) if part)
    definition_query = " ".join(
        part for part in (subject, context, conditions, conclusions, content, proof, title) if part
    )
    definition_support = " ".join(part for part in (title, label, formal, content, conclusions) if part)
    return {
        "title": title,
        "label": label,
        "proof": proof,
        "source_original_form": source_original_form,
        "derivation_context": derivation_context,
        "conditions": conditions,
        "condition_items": get_node_field_texts(node, "conditions"),
        "conclusions": conclusions,
        "conclusion_items": get_node_field_texts(node, "conclusions"),
        "subject": subject,
        "context": context,
        "content": content,
        "statement": statement,
        "logic_query": _normalized_text(logic_query),
        "logic_support": _normalized_text(logic_support),
        "definition_query": _normalized_text(definition_query),
        "definition_support": _normalized_text(definition_support),
    }


def tokenize_math_text(text):
    """Tokenize English, LaTeX, identifiers, and Chinese n-grams."""
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    tokens = []
    for match in _TOKEN_PATTERN.finditer(text):
        token = match.group(0)
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) == 1:
                continue
            for size in (2, 3):
                if len(token) < size:
                    continue
                tokens.extend(token[index:index + size] for index in range(len(token) - size + 1))
            continue
        if len(token) == 1 and token.isalpha():
            continue
        if token in _GENERIC_TOKENS:
            continue
        tokens.append(token)
    return tokens


def _weighted_document(views, kind):
    if kind == "logic":
        fields = ((views["conclusions"], 4), (views["title"], 2), (views["statement"], 1))
    else:
        fields = ((views["title"], 4), (views["label"], 3), (views["definition_support"], 2))
    counter = Counter()
    for text, weight in fields:
        for token in tokenize_math_text(text):
            counter[token] += weight
    return counter


class BM25FIndex:
    def __init__(self, documents):
        self.documents = documents
        self.document_frequency = Counter()
        self.lengths = {}
        for key, terms in documents.items():
            self.lengths[key] = sum(terms.values())
            self.document_frequency.update(terms.keys())
        self.document_count = max(1, len(documents))
        self.average_length = max(1.0, sum(self.lengths.values()) / self.document_count)

    def idf(self, token):
        frequency = self.document_frequency.get(token, 0)
        return math.log(1.0 + (self.document_count - frequency + 0.5) / (frequency + 0.5))

    def score(self, query_tokens, document_key, *, k1=1.5, b=0.75):
        terms = self.documents.get(document_key) or {}
        length = self.lengths.get(document_key, 0)
        score = 0.0
        for token in set(query_tokens):
            term_frequency = terms.get(token, 0)
            if term_frequency <= 0:
                continue
            denominator = term_frequency + k1 * (1.0 - b + b * length / self.average_length)
            score += self.idf(token) * (term_frequency * (k1 + 1.0)) / denominator
        return score


class EmbeddingCache:
    def __init__(self, output_dir, model, embed_texts, strict=True):
        self.output_dir = Path(output_dir) if output_dir else None
        self.model = str(model or "")
        self.embed_texts = embed_texts
        self.strict = strict
        self.path = self.output_dir / "relation_embedding_cache.json" if self.output_dir else None
        self.vectors = {}
        self.stats = {"requested": 0, "cache_hits": 0, "cache_misses": 0, "failed": 0}
        if self.path and self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if payload.get("schema_version") == EMBEDDING_CACHE_SCHEMA_VERSION:
                    self.vectors = payload.get("vectors") or {}
            except (OSError, json.JSONDecodeError, AttributeError):
                self.vectors = {}

    def _key(self, text):
        normalized = _normalized_text(text)
        digest = hashlib.sha256(f"{self.model}\0{normalized}".encode("utf-8")).hexdigest()
        return digest, normalized

    @staticmethod
    def _valid_vector(vector):
        if not isinstance(vector, list) or not vector:
            return False
        try:
            return all(math.isfinite(float(value)) for value in vector) and any(float(value) != 0.0 for value in vector)
        except (TypeError, ValueError):
            return False

    def embed_many(self, texts):
        unique = []
        keys = []
        seen = set()
        for text in texts:
            key, normalized = self._key(text)
            if not normalized or key in seen:
                continue
            seen.add(key)
            keys.append((key, normalized))
            unique.append(normalized)
        self.stats["requested"] += len(unique)

        misses = []
        miss_keys = []
        for key, normalized in keys:
            cached = self.vectors.get(key)
            if self._valid_vector(cached):
                self.stats["cache_hits"] += 1
            else:
                misses.append(normalized)
                miss_keys.append(key)
        self.stats["cache_misses"] += len(misses)

        if misses:
            if self.embed_texts is None:
                self.stats["failed"] += len(misses)
                raise EmbeddingRetrievalError("hybrid_strict retrieval requires an embedding provider")
            vectors = self.embed_texts(misses)
            if not isinstance(vectors, list) or len(vectors) != len(misses):
                vectors = []
            failures = []
            for index, key in enumerate(miss_keys):
                vector = vectors[index] if index < len(vectors) else []
                if self._valid_vector(vector):
                    self.vectors[key] = [float(value) for value in vector]
                else:
                    failures.append(index)
            self.stats["failed"] += len(failures)
            self.save()
            if failures and self.strict:
                raise EmbeddingRetrievalError(
                    f"embedding retrieval failed for {len(failures)} non-empty texts",
                    report={"embedding": dict(self.stats)},
                )

        result = {}
        for key, normalized in keys:
            result[normalized] = self.vectors.get(key, [])
        return result

    def save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": EMBEDDING_CACHE_SCHEMA_VERSION,
            "vectors": self.vectors,
        }
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temp_path, self.path)


def _cosine(left, right):
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _unit_parent_id(node):
    return str(node.get("parent_global_id") or node.get("global_id") or "").strip()


def _unit_identity(node, index):
    parent_id = _unit_parent_id(node) or f"index-{index}"
    sub_index = node.get("sub_index")
    return f"{parent_id}#sub{sub_index}" if sub_index is not None else parent_id


def _candidate_id(dependent, support, dependent_index, support_index, relation_kind):
    raw = "\0".join(
        (
            _unit_identity(dependent, dependent_index),
            _unit_identity(support, support_index),
            relation_kind,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _predicate_key(entry):
    for field_name in ("semantic_key", "canonical_name_key", "normalized_pred_id", "canonical_name", "pred_id"):
        value = str(entry.get(field_name) or "").strip().upper()
        if value and value not in _GENERIC_PREDICATES:
            return value
    return ""


def _predicate_signature_compatible(left, right):
    left_arity = left.get("arity")
    right_arity = right.get("arity")
    if left_arity is not None and right_arity is not None:
        try:
            if int(left_arity) != int(right_arity):
                return False
        except (TypeError, ValueError):
            pass
    left_sorts = left.get("arg_sorts") or []
    right_sorts = right.get("arg_sorts") or []
    if left_sorts and right_sorts and len(left_sorts) == len(right_sorts):
        for left_sort, right_sort in zip(left_sorts, right_sorts):
            if left_sort == right_sort or "Entity" in {left_sort, right_sort}:
                continue
            return False
    return True


def _predicate_entries_by_parent(node_list, predicate_entries):
    mapping = defaultdict(list)
    for entry in predicate_entries or []:
        if not isinstance(entry, dict):
            continue
        parent_id = str(entry.get("source_global_id") or "").strip()
        if parent_id:
            mapping[parent_id].append(entry)
    for node in node_list:
        parent_id = _unit_parent_id(node)
        for entry in node.get("predicate_entries") or []:
            if isinstance(entry, dict) and entry not in mapping[parent_id]:
                mapping[parent_id].append(entry)
    return mapping


def _matched_predicates(left_entries, right_entries):
    left_by_key = defaultdict(list)
    right_by_key = defaultdict(list)
    for entry in left_entries:
        key = _predicate_key(entry)
        if key:
            left_by_key[key].append(entry)
    for entry in right_entries:
        key = _predicate_key(entry)
        if key:
            right_by_key[key].append(entry)
    matches = []
    for key in sorted(set(left_by_key) & set(right_by_key)):
        if any(
            _predicate_signature_compatible(left, right)
            for left in left_by_key[key]
            for right in right_by_key[key]
        ):
            matches.append(key)
    return matches


def _alias_key(text):
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff\\]+", "", text)


def _usable_alias(alias):
    key = _alias_key(alias)
    if not key:
        return False
    if re.fullmatch(r"(?:definition|theorem|lemma|proposition|corollary|定义|定理|引理|命题|推论)[0-9.()]+", key):
        return True
    ascii_only = all(ord(char) < 128 for char in key)
    return len(key) >= (4 if ascii_only else 2)


def _definition_aliases(node, predicate_entries):
    values = []
    values.extend(node.get("reference_aliases") or [])
    values.extend((get_node_title(node), get_node_label(node)))
    for entry in predicate_entries:
        values.extend(
            entry.get(field_name)
            for field_name in ("canonical_name", "surface_template", "gloss")
            if entry.get(field_name)
        )
        values.extend(entry.get("surface_forms") or [])
    result = []
    seen = set()
    for value in values:
        value = str(value or "").strip()
        key = _alias_key(value)
        if _usable_alias(value) and key not in seen:
            seen.add(key)
            result.append((key, value))
    return result


def _ranked(scores, limit):
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]


def _distance_bucket(distance):
    if distance <= 5:
        return "1-5"
    if distance <= 30:
        return "6-30"
    return ">30"


def _valid_implicit_pair(node_list, dependent_index, support_index, relation_kind):
    if support_index >= dependent_index:
        return False
    dependent = node_list[dependent_index]
    support = node_list[support_index]
    if _unit_parent_id(dependent) and _unit_parent_id(dependent) == _unit_parent_id(support):
        return False
    support_type = (get_node_node_type(support) or "").strip()
    dependent_type = (get_node_node_type(dependent) or "").strip()
    if relation_kind == "definition":
        return is_definition_node_type(support_type)
    return is_relation_statement_node_type(support_type) and is_relation_statement_node_type(dependent_type)


def _add_channel(channel_rankings, dependent_index, relation_kind, channel, ranked_items):
    bucket = channel_rankings[dependent_index][relation_kind]
    bucket[channel] = [
        {
            "support_index": support_index,
            "score": float(score),
            **(metadata or {}),
        }
        for support_index, score, metadata in ranked_items
    ]


def _build_embedding_views(views, cache):
    texts = []
    for item in views:
        texts.extend((item["logic_query"], item["logic_support"], item["definition_query"], item["definition_support"]))
        texts.extend(_normalized_text(value) for value in item["condition_items"])
        texts.extend(_normalized_text(value) for value in item["conclusion_items"])
    vectors = cache.embed_many(texts)

    def vector(text):
        return vectors.get(_normalized_text(text), [])

    return [
        {
            "logic_query": vector(item["logic_query"]),
            "logic_support": vector(item["logic_support"]),
            "definition_query": vector(item["definition_query"]),
            "definition_support": vector(item["definition_support"]),
            "conditions": [vector(value) for value in item["condition_items"] if _normalized_text(value)],
            "conclusions": [vector(value) for value in item["conclusion_items"] if _normalized_text(value)],
        }
        for item in views
    ]


def _condition_conclusion_score(condition_vectors, conclusion_vectors):
    if not condition_vectors or not conclusion_vectors:
        return 0.0
    best_scores = [max(_cosine(condition, conclusion) for conclusion in conclusion_vectors) for condition in condition_vectors]
    return sum(best_scores) / len(best_scores)


def _build_report(config, node_count, embedding_stats=None):
    return {
        "schema_version": 1,
        "status": "retrieved",
        "publishable": config.mode == "hybrid_strict",
        "mode": config.mode,
        "config": asdict(config),
        "node_count": node_count,
        "embedding": embedding_stats or {"requested": 0, "cache_hits": 0, "cache_misses": 0, "failed": 0},
        "pre_rerank_candidate_count": 0,
        "selected_candidate_count": 0,
        "logic_candidate_count": 0,
        "definition_candidate_count": 0,
        "channel_candidate_counts": {},
        "distance_buckets": {"1-5": 0, "6-30": 0, ">30": 0},
        "per_dependent": {},
    }


def retrieve_relation_candidates(
    node_list,
    *,
    config=None,
    predicate_entries=None,
    excluded_pair_ids=None,
    explicit_supports=None,
    pair_mode="structured",
    embedding_model=None,
    embed_texts=None,
    output_dir=None,
):
    """Return pre-rerank candidates and an audit report."""
    config = (config or RelationRetrievalConfig()).validate()
    excluded_pair_ids = excluded_pair_ids or set()
    explicit_supports = explicit_supports or {}
    views = [_node_text_views(node, pair_mode=pair_mode) for node in node_list]
    report = _build_report(config, len(node_list))

    logic_documents = {
        index: _weighted_document(item, "logic")
        for index, item in enumerate(views)
        if is_relation_statement_node_type((get_node_node_type(node_list[index]) or "").strip())
    }
    definition_documents = {
        index: _weighted_document(item, "definition")
        for index, item in enumerate(views)
        if is_definition_node_type((get_node_node_type(node_list[index]) or "").strip())
    }
    logic_bm25 = BM25FIndex(logic_documents)
    definition_bm25 = BM25FIndex(definition_documents)

    predicate_mapping = _predicate_entries_by_parent(node_list, predicate_entries)
    predicate_parent_frequency = Counter()
    for parent_id, entries in predicate_mapping.items():
        predicate_parent_frequency.update({_predicate_key(entry) for entry in entries if _predicate_key(entry)})
    parent_count = max(1, len(predicate_mapping))

    all_token_sets = [set(tokenize_math_text(item["logic_query"] + " " + item["logic_support"])) for item in views]
    token_frequency = Counter()
    for tokens in all_token_sets:
        token_frequency.update(tokens)

    alias_frequency = Counter()
    aliases_by_index = {}
    for index, node in enumerate(node_list):
        if not is_definition_node_type((get_node_node_type(node) or "").strip()):
            continue
        aliases = _definition_aliases(node, predicate_mapping.get(_unit_parent_id(node), []))
        aliases_by_index[index] = aliases
        alias_frequency.update({key for key, _ in aliases})

    has_implicit_pairs = False
    for dependent_index, dependent in enumerate(node_list):
        dependent_parent = _unit_parent_id(dependent)
        for support_index in range(dependent_index):
            support_parent = _unit_parent_id(node_list[support_index])
            if (dependent_parent, support_parent) in excluded_pair_ids:
                continue
            if _valid_implicit_pair(node_list, dependent_index, support_index, "logic") or _valid_implicit_pair(
                node_list, dependent_index, support_index, "definition"
            ):
                has_implicit_pairs = True
                break
        if has_implicit_pairs:
            break

    embedding_views = None
    if config.mode == "hybrid_strict" and has_implicit_pairs:
        if not embedding_model:
            report["status"] = "embedding_failed"
            report["publishable"] = False
            raise EmbeddingRetrievalError("hybrid_strict retrieval requires embedding_model", report=report)
        cache = EmbeddingCache(output_dir, embedding_model, embed_texts, strict=True)
        try:
            embedding_views = _build_embedding_views(views, cache)
        except EmbeddingRetrievalError as exc:
            report["status"] = "embedding_failed"
            report["publishable"] = False
            report["embedding"] = dict(cache.stats)
            exc.report = report
            raise
        cache.save()
        report["embedding"] = dict(cache.stats)

    channel_rankings = defaultdict(lambda: defaultdict(dict))

    for dependent_index, dependent in enumerate(node_list):
        dependent_parent = _unit_parent_id(dependent)
        if not (views[dependent_index]["logic_query"] or views[dependent_index]["definition_query"]):
            continue

        logic_indices = [
            index
            for index in range(dependent_index)
            if _valid_implicit_pair(node_list, dependent_index, index, "logic")
            and (dependent_parent, _unit_parent_id(node_list[index])) not in excluded_pair_ids
        ]
        definition_indices = [
            index
            for index in range(dependent_index)
            if _valid_implicit_pair(node_list, dependent_index, index, "definition")
            and (dependent_parent, _unit_parent_id(node_list[index])) not in excluded_pair_ids
        ]

        local = list(reversed(logic_indices[-config.window_k:]))
        _add_channel(
            channel_rankings,
            dependent_index,
            "logic",
            "local_window",
            [(index, 1.0 / max(1, dependent_index - index), {}) for index in local],
        )

        proof_reference_text = _alias_key(
            " ".join(
                part
                for part in (
                    views[dependent_index]["proof"],
                    views[dependent_index]["derivation_context"],
                    views[dependent_index]["source_original_form"],
                )
                if part
            )
        )
        proof_reference_scores = {}
        proof_reference_metadata = {}
        for index in logic_indices:
            raw_title = node_list[index].get("title")
            title_aliases = (
                list(raw_title.values())
                if isinstance(raw_title, dict)
                else [raw_title]
            )
            aliases = list(
                dict.fromkeys(
                    value
                    for value in (
                        *title_aliases,
                        views[index]["title"],
                        views[index]["label"],
                    )
                    if _usable_alias(value)
                )
            )
            matches = [value for value in aliases if _alias_key(value) in proof_reference_text]
            if matches:
                proof_reference_scores[index] = max(len(_alias_key(value)) for value in matches)
                proof_reference_metadata[index] = {"matched_aliases": sorted(set(matches))}
        _add_channel(
            channel_rankings,
            dependent_index,
            "logic",
            "proof_reference",
            [
                (index, score, proof_reference_metadata[index])
                for index, score in _ranked(proof_reference_scores, config.logic_channel_k)
            ],
        )

        logic_query_tokens = tokenize_math_text(views[dependent_index]["logic_query"])
        logic_sparse_scores = {
            index: logic_bm25.score(logic_query_tokens, index)
            for index in logic_indices
        }
        _add_channel(
            channel_rankings,
            dependent_index,
            "logic",
            "bm25f",
            [(index, score, {}) for index, score in _ranked({i: s for i, s in logic_sparse_scores.items() if s > 0}, config.logic_channel_k)],
        )

        definition_query_tokens = tokenize_math_text(views[dependent_index]["definition_query"])
        definition_sparse_scores = {
            index: definition_bm25.score(definition_query_tokens, index)
            for index in definition_indices
        }
        _add_channel(
            channel_rankings,
            dependent_index,
            "definition",
            "bm25f",
            [
                (index, score, {})
                for index, score in _ranked(
                    {i: s for i, s in definition_sparse_scores.items() if s > 0},
                    config.definition_channel_k,
                )
            ],
        )

        usage_key = _alias_key(views[dependent_index]["definition_query"])
        exact_alias_scores = {}
        exact_alias_metadata = {}
        for index in definition_indices:
            matches = [
                original
                for key, original in aliases_by_index.get(index, [])
                if alias_frequency[key] == 1 and key in usage_key
            ]
            if matches:
                exact_alias_scores[index] = max(len(_alias_key(value)) for value in matches)
                exact_alias_metadata[index] = {"matched_aliases": sorted(set(matches))}
        _add_channel(
            channel_rankings,
            dependent_index,
            "definition",
            "exact_alias",
            [
                (index, score, exact_alias_metadata[index])
                for index, score in _ranked(exact_alias_scores, config.definition_channel_k)
            ],
        )

        dependent_predicates = predicate_mapping.get(dependent_parent, [])
        for relation_kind, indices, limit in (
            ("logic", logic_indices, config.logic_channel_k),
            ("definition", definition_indices, config.definition_channel_k),
        ):
            predicate_scores = {}
            predicate_metadata = {}
            for index in indices:
                support_predicates = predicate_mapping.get(_unit_parent_id(node_list[index]), [])
                matches = [
                    key
                    for key in _matched_predicates(dependent_predicates, support_predicates)
                    if predicate_parent_frequency[key] <= max(3, math.ceil(parent_count * 0.20))
                ]
                dependent_tokens = all_token_sets[dependent_index]
                support_tokens = all_token_sets[index]
                symbol_matches = sorted(
                    token
                    for token in dependent_tokens & support_tokens
                    if _LATEX_OR_IDENTIFIER_PATTERN.match(token)
                    and token_frequency[token] <= max(3, math.ceil(len(node_list) * 0.20))
                )
                score = sum(
                    math.log(1.0 + parent_count / max(1, predicate_parent_frequency[key])) for key in matches
                )
                score += sum(math.log(1.0 + len(node_list) / max(1, token_frequency[token])) for token in symbol_matches)
                if score > 0:
                    predicate_scores[index] = score
                    predicate_metadata[index] = {
                        "matched_predicates": matches,
                        "matched_symbols": symbol_matches[:12],
                    }
            _add_channel(
                channel_rankings,
                dependent_index,
                relation_kind,
                "predicate_symbol",
                [
                    (index, score, predicate_metadata[index])
                    for index, score in _ranked(predicate_scores, limit)
                ],
            )

        if embedding_views is not None:
            dense_logic_scores = {
                index: _cosine(
                    embedding_views[dependent_index]["logic_query"],
                    embedding_views[index]["logic_support"],
                )
                for index in logic_indices
            }
            _add_channel(
                channel_rankings,
                dependent_index,
                "logic",
                "dense_statement",
                [(index, score, {}) for index, score in _ranked(dense_logic_scores, config.logic_channel_k)],
            )
            coverage_scores = {
                index: _condition_conclusion_score(
                    embedding_views[dependent_index]["conditions"],
                    embedding_views[index]["conclusions"],
                )
                for index in logic_indices
            }
            _add_channel(
                channel_rankings,
                dependent_index,
                "logic",
                "condition_conclusion",
                [(index, score, {}) for index, score in _ranked(coverage_scores, config.logic_channel_k)],
            )
            dense_definition_scores = {
                index: _cosine(
                    embedding_views[dependent_index]["definition_query"],
                    embedding_views[index]["definition_support"],
                )
                for index in definition_indices
            }
            _add_channel(
                channel_rankings,
                dependent_index,
                "definition",
                "dense_statement",
                [
                    (index, score, {})
                    for index, score in _ranked(dense_definition_scores, config.definition_channel_k)
                ],
            )

        seed_ranks = {}
        for relation_channels in channel_rankings[dependent_index].values():
            for items in relation_channels.values():
                for rank, item in enumerate(items[:5], start=1):
                    support_index = item["support_index"]
                    seed_ranks[support_index] = min(seed_ranks.get(support_index, 10**9), rank)
        expanded = {}
        for seed_index, seed_rank in seed_ranks.items():
            seed_parent = _unit_parent_id(node_list[seed_index])
            for support_parent in explicit_supports.get(seed_parent, set()):
                for support_index in range(dependent_index):
                    if _unit_parent_id(node_list[support_index]) != support_parent:
                        continue
                    if (dependent_parent, support_parent) in excluded_pair_ids:
                        continue
                    distance = dependent_index - support_index
                    score = 1.0 / (seed_rank + math.log1p(distance))
                    expanded[support_index] = max(expanded.get(support_index, 0.0), score)
        logic_expanded = {
            index: score
            for index, score in expanded.items()
            if _valid_implicit_pair(node_list, dependent_index, index, "logic")
        }
        definition_expanded = {
            index: score
            for index, score in expanded.items()
            if _valid_implicit_pair(node_list, dependent_index, index, "definition")
        }
        _add_channel(
            channel_rankings,
            dependent_index,
            "logic",
            "explicit_graph_expand",
            [(index, score, {}) for index, score in _ranked(logic_expanded, config.graph_expand_k)],
        )
        _add_channel(
            channel_rankings,
            dependent_index,
            "definition",
            "explicit_graph_expand",
            [(index, score, {}) for index, score in _ranked(definition_expanded, config.graph_expand_k)],
        )

    all_candidates = []
    per_dependent_candidates = defaultdict(dict)
    channel_counts = Counter()
    for dependent_index, by_kind in channel_rankings.items():
        for relation_kind, channels in by_kind.items():
            for channel, items in channels.items():
                for rank, item in enumerate(items, start=1):
                    support_index = item["support_index"]
                    key = (support_index, relation_kind)
                    candidate = per_dependent_candidates[dependent_index].get(key)
                    if candidate is None:
                        candidate = RelationCandidate(
                            candidate_id=_candidate_id(
                                node_list[dependent_index],
                                node_list[support_index],
                                dependent_index,
                                support_index,
                                relation_kind,
                            ),
                            dependent_global_id=_unit_parent_id(node_list[dependent_index]),
                            support_global_id=_unit_parent_id(node_list[support_index]),
                            dependent_index=dependent_index,
                            support_index=support_index,
                            relation_kind=relation_kind,
                        )
                        per_dependent_candidates[dependent_index][key] = candidate
                    candidate.retrieval_channels.append(channel)
                    candidate.channel_ranks[channel] = rank
                    candidate.channel_scores[channel] = float(item["score"])
                    candidate.matched_predicates.extend(item.get("matched_predicates") or [])
                    candidate.matched_symbols.extend(item.get("matched_symbols") or [])
                    candidate.matched_aliases.extend(item.get("matched_aliases") or [])
                    candidate.rrf_score += 1.0 / (config.rrf_k + rank)
                    if channel == "exact_alias":
                        candidate.protected = True
                    channel_counts[channel] += 1

    for dependent_index, mapping in per_dependent_candidates.items():
        values = list(mapping.values())
        for channel in {name for candidate in values for name in candidate.retrieval_channels}:
            top_channel = sorted(
                (candidate for candidate in values if channel in candidate.channel_ranks),
                key=lambda candidate: candidate.channel_ranks[channel],
            )[:config.per_channel_protected_k]
            for candidate in top_channel:
                candidate.protected = True
        ordered = sorted(
            values,
            key=lambda candidate: (
                not bool(candidate.matched_aliases),
                not candidate.protected,
                -candidate.rrf_score,
                dependent_index - candidate.support_index,
                candidate.candidate_id,
            ),
        )
        selected = []
        definition_count = 0
        for candidate in ordered:
            if len(selected) >= config.pre_rerank_k:
                break
            if candidate.relation_kind == "definition":
                if definition_count >= config.definition_pre_rerank_k:
                    continue
                definition_count += 1
            candidate.retrieval_channels = sorted(set(candidate.retrieval_channels))
            candidate.matched_predicates = sorted(set(candidate.matched_predicates))
            candidate.matched_symbols = sorted(set(candidate.matched_symbols))
            candidate.matched_aliases = sorted(set(candidate.matched_aliases))
            selected.append(candidate)
        all_candidates.extend(selected)

    parent_grouped = defaultdict(list)
    for candidate in all_candidates:
        parent_key = candidate.dependent_global_id or f"index-{candidate.dependent_index}"
        parent_grouped[parent_key].append(candidate)
    parent_capped_candidates = []
    for parent_key in sorted(parent_grouped):
        ordered = sorted(
            parent_grouped[parent_key],
            key=lambda candidate: (
                not bool(candidate.matched_aliases),
                not candidate.protected,
                -candidate.rrf_score,
                candidate.dependent_index - candidate.support_index,
                candidate.candidate_id,
            ),
        )
        definition_count = 0
        selected_for_parent = []
        for candidate in ordered:
            if len(selected_for_parent) >= config.pre_rerank_k:
                break
            if candidate.relation_kind == "definition":
                if definition_count >= config.definition_pre_rerank_k:
                    continue
                definition_count += 1
            selected_for_parent.append(candidate)
        parent_capped_candidates.extend(selected_for_parent)
    all_candidates = parent_capped_candidates

    report["pre_rerank_candidate_count"] = len(all_candidates)
    report["logic_candidate_count"] = sum(candidate.relation_kind == "logic" for candidate in all_candidates)
    report["definition_candidate_count"] = sum(candidate.relation_kind == "definition" for candidate in all_candidates)
    report["channel_candidate_counts"] = dict(sorted(channel_counts.items()))
    for candidate in all_candidates:
        report["distance_buckets"][_distance_bucket(candidate.dependent_index - candidate.support_index)] += 1
        key = str(candidate.dependent_global_id or candidate.dependent_index)
        row = report["per_dependent"].setdefault(key, {"pre_rerank": 0, "selected": 0})
        row["pre_rerank"] += 1
    return all_candidates, report


def build_rerank_tasks(
    candidates,
    node_list,
    batch_size=20,
    logic_prompt_contract_version=LOGIC_PROMPT_CONTRACT_VERSION,
):
    grouped = defaultdict(list)
    for candidate in candidates:
        grouped[(candidate.dependent_index, candidate.relation_kind)].append(candidate)
    tasks = {}
    for (dependent_index, relation_kind), items in sorted(grouped.items()):
        items = sorted(items, key=lambda item: (-item.rrf_score, item.candidate_id))
        dependent_view = _node_text_views(node_list[dependent_index])
        dependent_summary = {
            "global_id": _unit_parent_id(node_list[dependent_index]),
            "title": dependent_view["title"],
            "content": dependent_view["content"],
            "source_original_form": dependent_view["source_original_form"],
            "conditions": dependent_view["condition_items"],
            "conclusions": dependent_view["conclusion_items"],
            "proof": dependent_view["proof"][:500],
            "derivation_context": dependent_view["derivation_context"][:2000],
        }
        for start in range(0, len(items), batch_size):
            batch = items[start:start + batch_size]
            candidate_summaries = []
            for candidate in batch:
                support_view = _node_text_views(node_list[candidate.support_index])
                candidate_summaries.append(
                    {
                        "candidate_id": candidate.candidate_id,
                        "title": support_view["title"],
                        "label": support_view["label"],
                        "node_type": get_node_node_type(node_list[candidate.support_index]),
                        "content": support_view["content"][:800],
                        "conclusions_or_definition": (
                            support_view["conclusion_items"] or [support_view["definition_support"][:600]]
                        ),
                        "retrieval_channels": candidate.retrieval_channels,
                        "matched_predicates": candidate.matched_predicates,
                        "matched_symbols": candidate.matched_symbols,
                        "matched_aliases": candidate.matched_aliases,
                    }
                )
            fingerprint_payload = {
                "logic_prompt_contract_version": (
                    logic_prompt_contract_version if relation_kind == "logic" else None
                ),
                "dependent": dependent_summary,
                "relation_kind": relation_kind,
                "candidates": candidate_summaries,
            }
            fingerprint = hashlib.sha256(
                json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            tasks[fingerprint] = {
                "dependent_json": json.dumps(dependent_summary, ensure_ascii=False),
                "relation_kind": relation_kind,
                "logic_prompt_contract_version": (
                    logic_prompt_contract_version if relation_kind == "logic" else None
                ),
                "candidates_json": json.dumps(candidate_summaries, ensure_ascii=False),
                "candidate_ids": [candidate.candidate_id for candidate in batch],
            }
    return tasks


def normalize_rerank_result(task, result):
    if not isinstance(result, dict) or not isinstance(result.get("ranked"), list):
        return None
    expected = list(task.get("candidate_ids") or [])
    if len(expected) != len(set(expected)):
        return None
    normalized_ranked = []
    for item in result["ranked"]:
        if not isinstance(item, dict):
            return None
        candidate_id = item.get("candidate_id")
        score = item.get("score")
        try:
            score = float(score)
        except (TypeError, ValueError):
            return None
        if score < 0 or score > 3 or not isinstance(candidate_id, str):
            return None
        normalized_ranked.append({**item, "candidate_id": candidate_id})

    actual = [item["candidate_id"] for item in normalized_ranked]
    if len(actual) != len(expected) or len(actual) != len(set(actual)):
        return None
    if set(actual) == set(expected):
        return {**result, "ranked": normalized_ranked}

    missing = set(expected) - set(actual)
    unexpected = set(actual) - set(expected)
    if len(missing) != 1 or len(unexpected) != 1:
        return None
    missing_id = next(iter(missing))
    unexpected_id = next(iter(unexpected))
    if not (
        re.fullmatch(r"[0-9a-f]{24}", missing_id)
        and re.fullmatch(r"[0-9a-f]{24}", unexpected_id)
        and sum(left != right for left, right in zip(missing_id, unexpected_id)) == 1
    ):
        return None

    repaired_ranked = [
        {**item, "candidate_id": missing_id}
        if item["candidate_id"] == unexpected_id
        else item
        for item in normalized_ranked
    ]
    return {**result, "ranked": repaired_ranked}


def validate_rerank_result(task, result):
    return normalize_rerank_result(task, result) is not None


def apply_rerank_results(candidates, tasks, results):
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    for task_key, task in tasks.items():
        result = (results or {}).get(task_key) or (results or {}).get(str(task_key))
        result = normalize_rerank_result(task, result)
        if result is None:
            continue
        for rank, item in enumerate(result["ranked"], start=1):
            candidate = by_id.get(item["candidate_id"])
            if candidate is not None:
                candidate.rerank_score = float(item["score"])
                candidate.rerank_rank = rank
    return candidates


def select_final_candidates(candidates, config=None):
    config = (config or RelationRetrievalConfig()).validate()
    grouped = defaultdict(list)
    for candidate in candidates:
        candidate.selected = False
        dependent_key = candidate.dependent_global_id or f"index-{candidate.dependent_index}"
        grouped[dependent_key].append(candidate)

    selected = []

    def order(items):
        return sorted(
            items,
            key=lambda candidate: (
                not bool(candidate.matched_aliases),
                not candidate.protected,
                -(candidate.rerank_score if candidate.rerank_score is not None else -1.0),
                candidate.rerank_rank if candidate.rerank_rank is not None else 10**9,
                -candidate.rrf_score,
                candidate.dependent_index - candidate.support_index,
                candidate.candidate_id,
            ),
        )

    for dependent_key in sorted(grouped):
        values = grouped[dependent_key]
        definitions = order([candidate for candidate in values if candidate.relation_kind == "definition"])
        selected_definitions = definitions[:config.definition_final_k]
        logic_budget = config.logic_final_k + (config.definition_final_k - len(selected_definitions))
        logic = order([candidate for candidate in values if candidate.relation_kind == "logic"])
        selected_logic = logic[:logic_budget]
        for candidate in selected_logic + selected_definitions:
            candidate.selected = True
            selected.append(candidate)
    return selected


def finalize_retrieval_report(report, candidates, selected):
    report = dict(report or {})
    report["selected_candidate_count"] = len(selected)
    report["selected_logic_candidate_count"] = sum(item.relation_kind == "logic" for item in selected)
    report["selected_definition_candidate_count"] = sum(item.relation_kind == "definition" for item in selected)
    for row in (report.get("per_dependent") or {}).values():
        row["selected"] = 0
    for candidate in selected:
        key = str(candidate.dependent_global_id or candidate.dependent_index)
        row = report.setdefault("per_dependent", {}).setdefault(key, {"pre_rerank": 0, "selected": 0})
        row["selected"] += 1
    report["status"] = "selected"
    report["publishable"] = report.get("mode") == "hybrid_strict"
    return report


def _normalize_text_items(items):
    if not isinstance(items, list):
        return []
    return [
        {"text": item.get("text", ""), "text_normalized": item.get("text_normalized", item.get("text", ""))}
        for item in items
        if isinstance(item, dict)
    ]


def _build_full_pair_side(node):
    return {
        "global_id": node.get("global_id", ""),
        "parent_global_id": node.get("parent_global_id", node.get("global_id", "")),
        "sub_index": node.get("sub_index"),
        "is_virtual_subnode": node.get("is_virtual_subnode", True),
        "node_type": get_node_node_type(node),
        "title": node.get("title", {}),
        "label": get_node_label(node),
        "content": get_node_formal_content(node),
        "source_original_form": _source_original_form(node),
        "derivation_context": _flatten_text(node.get("derivation_context"))[:2000],
        "subject": _flatten_text(node.get("subject")),
        "context": _flatten_text(node.get("context")),
        "variables": node.get("variables") if isinstance(node.get("variables"), list) else [],
        "conditions": _normalize_text_items(node.get("conditions")),
        "conclusions": _normalize_text_items(node.get("conclusions")),
        "logic_ast_local": node.get("logic_ast_local") if isinstance(node.get("logic_ast_local"), dict) else {},
        "predicate_entries": node.get("predicate_entries") if isinstance(node.get("predicate_entries"), list) else [],
        "proof": get_node_proof(node),
        "reference_signals": (
            node.get("reference_signals") if isinstance(node.get("reference_signals"), dict) else {}
        ),
    }


def _build_definition_pair_side(node):
    side = _build_full_pair_side(node)
    return {
        key: side[key]
        for key in (
            "global_id",
            "parent_global_id",
            "sub_index",
            "is_virtual_subnode",
            "node_type",
            "title",
            "label",
            "content",
            "conditions",
            "conclusions",
            "predicate_entries",
            "proof",
        )
    }


def _build_natural_pair_side(node):
    return {
        "global_id": node.get("global_id", ""),
        "parent_global_id": node.get("parent_global_id", node.get("global_id", "")),
        "sub_index": node.get("sub_index"),
        "is_virtual_subnode": node.get("is_virtual_subnode", True),
        "node_type": get_node_node_type(node),
        "content": get_node_content(node),
        "source_original_form": _source_original_form(node),
        "derivation_context": _flatten_text(node.get("derivation_context"))[:2000],
        "proof": get_node_proof(node),
        "title": node.get("title", {}),
        "label": get_node_label(node),
    }


def build_entity_pairs(
    selected_candidates,
    node_list,
    pair_mode="structured",
    logic_prompt_contract_version=LOGIC_PROMPT_CONTRACT_VERSION,
):
    proof_pairs = {}
    definition_pairs = {}
    proof_counter = 0
    definition_counter = 0
    for candidate in sorted(
        selected_candidates,
        key=lambda item: (item.dependent_index, item.relation_kind, item.support_index, item.candidate_id),
    ):
        support = node_list[candidate.support_index]
        dependent = node_list[candidate.dependent_index]
        if pair_mode == "natural":
            builder = _build_natural_pair_side
        elif candidate.relation_kind == "definition":
            builder = _build_definition_pair_side
        else:
            builder = _build_full_pair_side
        payload = {
            "pos1": builder(support),
            "pos2": builder(dependent),
            "candidate_id": candidate.candidate_id,
            "retrieval_evidence": {
                "channels": candidate.retrieval_channels,
                "matched_predicates": candidate.matched_predicates,
                "matched_symbols": candidate.matched_symbols,
                "matched_aliases": candidate.matched_aliases,
            },
        }
        if candidate.relation_kind == "logic":
            payload["logic_prompt_contract_version"] = logic_prompt_contract_version
        if candidate.relation_kind == "definition":
            definition_pairs[definition_counter] = payload
            definition_counter += 1
        else:
            proof_pairs[proof_counter] = payload
            proof_counter += 1
    return proof_pairs, definition_pairs
