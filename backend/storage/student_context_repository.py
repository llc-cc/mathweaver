"""学生证明交互、证据链与可重建上下文投影仓储。"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

from storage.database import session_scope
from storage.models import (
    ClassMembership,
    EducationNodeIdentity,
    EducationNodeOccurrence,
    LearningContextSummary,
    LearningEvidence,
    LearningEvidenceFeedback,
    LearningEvidenceNode,
    LearningInteraction,
    StudentNodeModel,
    TeachingClass,
    utc_now,
)


EVIDENCE_KINDS = {
    "goal",
    "understanding",
    "misconception",
    "gap",
    "used_node",
    "hint",
    "unresolved_question",
    "strategy",
}
RISK_KINDS = {"misconception", "gap", "unresolved_question"}
FEEDBACK_STATUSES = {"open", "resolved", "retracted"}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _estimate_tokens(value) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    cjk = len(re.findall(r"[\u3400-\u9fff\uf900-\ufaff]", text))
    return max(1, cjk + math.ceil((len(text) - cjk) / 4))


def _node_title(node: dict) -> str:
    return str(
        node.get("title_zh")
        or node.get("title_en")
        or node.get("label")
        or f"节点 {node.get('id', '')}"
    ).strip()


class StudentContextRepository:
    """原始交互追加写；节点模型和摘要均可由证据重新构建。"""

    def __init__(self, session_factory=session_scope) -> None:
        self._session_factory = session_factory

    def build_overview(self, assignment: dict, snapshot: dict, user_id: int) -> dict:
        with self._session_factory() as session:
            teaching_class = self._class(session, assignment["class_id"])
            occurrences = session.scalars(
                select(EducationNodeOccurrence).where(
                    EducationNodeOccurrence.snapshot_id == snapshot["id"]
                )
            ).all()
            by_canonical = {
                row.canonical_node_id: row.node_id for row in occurrences
            }
            nodes = {
                int(item["id"]): item
                for item in snapshot.get("nodes_json") or []
                if isinstance(item, dict) and isinstance(item.get("id"), int)
            }
            models = session.scalars(
                select(StudentNodeModel).where(
                    StudentNodeModel.teaching_class_id == teaching_class.id,
                    StudentNodeModel.user_id == user_id,
                    StudentNodeModel.canonical_node_id.in_(by_canonical),
                )
            ).all() if by_canonical else []
            states = [
                self._model_payload(
                    model,
                    by_canonical[model.canonical_node_id],
                    _node_title(nodes.get(by_canonical[model.canonical_node_id], {})),
                )
                for model in models
            ]
            states.sort(key=lambda item: item["nodeId"])
            return {
                "contextVersion": self._context_version(
                    session, teaching_class.id, user_id
                ),
                "nodeStates": states,
            }

    def build_packet(
        self,
        assignment: dict,
        snapshot: dict,
        user_id: int,
        node_id: int,
        *,
        user_proof: str = "",
        action: str = "",
    ) -> dict:
        with self._session_factory() as session:
            teaching_class = self._class(session, assignment["class_id"])
            occurrence = session.get(
                EducationNodeOccurrence, (snapshot["id"], node_id)
            )
            nodes = {
                int(item["id"]): item
                for item in snapshot.get("nodes_json") or []
                if isinstance(item, dict) and isinstance(item.get("id"), int)
            }
            node = nodes.get(node_id)
            if occurrence is None or node is None:
                raise ValueError("node not found in assignment snapshot")
            linked = session.execute(
                select(LearningEvidence, LearningEvidenceNode)
                .join(
                    LearningEvidenceNode,
                    LearningEvidenceNode.evidence_id == LearningEvidence.id,
                )
                .where(
                    LearningEvidence.teaching_class_id == teaching_class.id,
                    LearningEvidence.user_id == user_id,
                    LearningEvidenceNode.canonical_node_id
                    == occurrence.canonical_node_id,
                )
                .order_by(
                    LearningEvidence.updated_at.desc(),
                    LearningEvidenceNode.weight.desc(),
                )
            ).all()
            evidence = [self._evidence_payload(*row, node_id=node_id) for row in linked]
            direct = [
                item
                for item in evidence
                if item["relationRole"] == "direct"
                and item["status"] in {"open", "confirmed"}
            ]
            related_risks = [
                item
                for item in evidence
                if item["relationRole"] != "direct"
                and item["status"] == "open"
                and item["kind"] in RISK_KINDS
            ]
            related = [
                item
                for item in evidence
                if item["relationRole"] != "direct"
                and item["status"] in {"open", "confirmed"}
                and item["kind"] not in RISK_KINDS
            ]
            resolved = [
                item
                for item in evidence
                if item["status"] in {"resolved", "retracted"}
            ][:12]
            interactions = session.scalars(
                select(LearningInteraction)
                .where(
                    LearningInteraction.teaching_class_id == teaching_class.id,
                    LearningInteraction.user_id == user_id,
                )
                .order_by(
                    LearningInteraction.created_at.desc(),
                    LearningInteraction.id.desc(),
                )
                .limit(4)
            ).all()
            recent = [
                {
                    "id": row.id,
                    "nodeId": row.node_id,
                    "action": row.action,
                    "studentProof": row.user_proof[-1200:],
                    "assistantResponse": row.assistant_response[-900:],
                    "createdAt": _iso(row.created_at),
                }
                for row in interactions
            ]
            model = session.get(
                StudentNodeModel,
                (teaching_class.id, user_id, occurrence.canonical_node_id),
            )
            current_state = (
                self._model_payload(model, node_id, _node_title(node))
                if model
                else {
                    "nodeId": node_id,
                    "title": _node_title(node),
                    "masteryState": "unknown",
                    "openEvidenceCount": 0,
                    "version": 0,
                    "updatedAt": None,
                }
            )
            summary = session.get(
                LearningContextSummary,
                (teaching_class.id, user_id, "course", assignment["class_id"]),
            )
            packet = {
                "schemaVersion": 1,
                "contextVersion": self._context_version(
                    session, teaching_class.id, user_id
                ),
                "courseId": assignment["class_id"],
                "assignmentId": assignment["id"],
                "currentNode": {
                    "nodeId": node_id,
                    "title": _node_title(node),
                    "nodeType": node.get("node_type") or "",
                    "statement": node.get("content")
                    or node.get("source_statement")
                    or "",
                    "conditions": node.get("conditions") or [],
                    "conclusions": node.get("conclusions") or [],
                    "action": action,
                    "studentProof": user_proof,
                },
                "currentState": current_state,
                "directEvidence": direct,
                "relatedEvidence": related,
                "relatedRisks": related_risks,
                "recentInteractions": recent,
                "resolvedItems": resolved,
                "courseSummary": deepcopy(summary.summary_json or {}) if summary else {},
                "compressedEvidenceRefs": [],
            }
            packet["historyTokenEstimate"] = _estimate_tokens(
                {
                    key: packet[key]
                    for key in (
                        "currentState",
                        "directEvidence",
                        "relatedEvidence",
                        "relatedRisks",
                        "recentInteractions",
                        "resolvedItems",
                        "courseSummary",
                    )
                }
            )
            packet["tokenEstimate"] = _estimate_tokens(packet)
            return packet

    def load_idempotent_result(
        self, assignment_id: str, user_id: int, client_interaction_id: str
    ) -> dict | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(LearningInteraction).where(
                    LearningInteraction.assignment_id == assignment_id,
                    LearningInteraction.user_id == user_id,
                    LearningInteraction.client_interaction_id
                    == client_interaction_id,
                )
            )
            if row is None:
                return None
            return deepcopy(row.result_json or {}) or None

    def interaction_exists(
        self, assignment_id: str, user_id: int, client_interaction_id: str
    ) -> bool:
        with self._session_factory() as session:
            return session.scalar(
                select(LearningInteraction.id).where(
                    LearningInteraction.assignment_id == assignment_id,
                    LearningInteraction.user_id == user_id,
                    LearningInteraction.client_interaction_id
                    == client_interaction_id,
                )
            ) is not None

    def store_interaction_with_evidence(
        self,
        *,
        assignment: dict,
        snapshot: dict,
        user_id: int,
        node_id: int,
        client_interaction_id: str,
        action: str,
        user_proof: str,
        assistant_response: str,
        context_packet: dict,
        learning_delta: list[dict],
        classification_status: str,
    ) -> dict:
        with self._session_factory() as session:
            teaching_class = self._class(session, assignment["class_id"])
            occurrence = session.get(
                EducationNodeOccurrence, (snapshot["id"], node_id)
            )
            if occurrence is None:
                raise ValueError("node not found in assignment snapshot")
            version = self._context_version(session, teaching_class.id, user_id) + 1
            interaction = LearningInteraction(
                id=uuid.uuid4().hex,
                client_interaction_id=client_interaction_id,
                user_id=user_id,
                teaching_class_id=teaching_class.id,
                assignment_id=assignment["id"],
                snapshot_id=snapshot["id"],
                canonical_node_id=occurrence.canonical_node_id,
                node_id=node_id,
                action=action,
                user_proof=user_proof,
                assistant_response=assistant_response,
                context_version=version,
                context_snapshot_json=deepcopy(context_packet),
                classification_status=classification_status,
                token_estimate=_estimate_tokens(user_proof)
                + _estimate_tokens(assistant_response),
                result_json={},
            )
            session.add(interaction)
            changes = []
            affected = {occurrence.canonical_node_id}
            occurrence_by_node = {
                row.node_id: row
                for row in session.scalars(
                    select(EducationNodeOccurrence).where(
                        EducationNodeOccurrence.snapshot_id == snapshot["id"]
                    )
                ).all()
            }
            for item in learning_delta:
                kind = item.get("kind")
                claim = str(item.get("claim") or "").strip()
                if kind not in EVIDENCE_KINDS or not claim:
                    continue
                try:
                    confidence = min(1.0, max(0.0, float(item.get("confidence", 0.5))))
                except (TypeError, ValueError):
                    confidence = 0.5
                evidence = LearningEvidence(
                    id=uuid.uuid4().hex,
                    interaction_id=interaction.id,
                    user_id=user_id,
                    teaching_class_id=teaching_class.id,
                    canonical_node_id=occurrence.canonical_node_id,
                    kind=kind,
                    claim=claim[:1200],
                    status="confirmed" if kind == "understanding" else "open",
                    source_type="ai",
                    confidence=confidence,
                    severity=(
                        item.get("severity")
                        if item.get("severity") in {"low", "medium", "high"}
                        else "medium"
                    ),
                    evidence_excerpt=user_proof[-600:],
                )
                session.add(evidence)
                session.add(
                    LearningEvidenceNode(
                        evidence_id=evidence.id,
                        canonical_node_id=occurrence.canonical_node_id,
                        relation_role="direct",
                        relation_path_json={},
                        weight=1.0,
                    )
                )
                linked = set()
                for related_node_id in item.get("relatedNodeIds") or []:
                    related = occurrence_by_node.get(related_node_id)
                    if related and related.canonical_node_id != occurrence.canonical_node_id:
                        session.add(
                            LearningEvidenceNode(
                                evidence_id=evidence.id,
                                canonical_node_id=related.canonical_node_id,
                                relation_role=(
                                    "prerequisite_risk"
                                    if kind in RISK_KINDS
                                    else "related"
                                ),
                                relation_path_json={
                                    "fromNodeId": node_id,
                                    "toNodeId": related_node_id,
                                },
                                weight=round(0.5 * confidence, 4),
                            )
                        )
                        linked.add(related.canonical_node_id)
                affected.update(linked)
                changes.append(
                    {
                        "evidenceId": evidence.id,
                        "kind": kind,
                        "claim": evidence.claim,
                        "status": evidence.status,
                        "affectedNodeCount": len(linked) + 1,
                    }
                )
            session.flush()
            models = [
                self._refresh_model(session, teaching_class.id, user_id, canonical)
                for canonical in affected
            ]
            self._refresh_course_summary(session, teaching_class.id, assignment["class_id"], user_id)
            return {
                "interactionId": interaction.id,
                "contextVersion": version,
                "stateChanges": changes,
                "models": models,
            }

    def save_interaction_result(self, interaction_id: str, result: dict) -> None:
        with self._session_factory() as session:
            row = session.get(LearningInteraction, interaction_id)
            if row:
                row.result_json = deepcopy(result)

    def update_evidence_status(
        self, evidence_id: str, user_id: int, new_status: str, note: str = ""
    ) -> dict | None:
        if new_status not in FEEDBACK_STATUSES:
            raise ValueError("invalid evidence status")
        with self._session_factory() as session:
            evidence = session.scalar(
                select(LearningEvidence)
                .where(
                    LearningEvidence.id == evidence_id,
                    LearningEvidence.user_id == user_id,
                )
                .with_for_update()
            )
            if evidence is None:
                return None
            previous = evidence.status
            evidence.status = new_status
            evidence.updated_at = utc_now()
            session.add(
                LearningEvidenceFeedback(
                    id=uuid.uuid4().hex,
                    evidence_id=evidence_id,
                    user_id=user_id,
                    action="reopen" if new_status == "open" else new_status,
                    previous_status=previous,
                    new_status=new_status,
                    note=note[:500],
                )
            )
            linked = session.scalars(
                select(LearningEvidenceNode).where(
                    LearningEvidenceNode.evidence_id == evidence_id
                )
            ).all()
            for item in linked:
                self._refresh_model(
                    session,
                    evidence.teaching_class_id,
                    user_id,
                    item.canonical_node_id,
                )
            teaching_class = session.get(TeachingClass, evidence.teaching_class_id)
            if teaching_class is not None:
                self._refresh_course_summary(
                    session,
                    evidence.teaching_class_id,
                    teaching_class.public_id,
                    user_id,
                )
            return {
                "id": evidence_id,
                "status": new_status,
                "previousStatus": previous,
                "updatedAt": _iso(evidence.updated_at),
            }

    def teacher_summary(
        self, assignment: dict, snapshot: dict, student_user_id: int
    ) -> dict:
        overview = self.build_overview(assignment, snapshot, student_user_id)
        with self._session_factory() as session:
            teaching_class = self._class(session, assignment["class_id"])
            rows = session.scalars(
                select(LearningEvidence)
                .where(
                    LearningEvidence.teaching_class_id == teaching_class.id,
                    LearningEvidence.user_id == student_user_id,
                    LearningEvidence.status.in_(("open", "confirmed")),
                )
                .order_by(LearningEvidence.updated_at.desc())
                .limit(30)
            ).all()
            summary = session.get(
                LearningContextSummary,
                (teaching_class.id, student_user_id, "course", assignment["class_id"]),
            )
            return {
                **overview,
                "courseSummary": deepcopy(summary.summary_json or {}) if summary else {},
                "courseSummaryUpdatedAt": _iso(summary.updated_at) if summary else None,
                "evidence": [
                    {
                        "id": row.id,
                        "kind": row.kind,
                        "claim": row.claim,
                        "status": row.status,
                        "confidence": float(row.confidence),
                        "severity": row.severity,
                        "excerpt": row.evidence_excerpt[:240],
                        "updatedAt": _iso(row.updated_at),
                    }
                    for row in rows
                ],
            }

    def data_rights_membership(self, public_class_id: str, user_id: int) -> dict | None:
        with self._session_factory() as session:
            row = session.execute(
                select(TeachingClass, ClassMembership)
                .join(
                    ClassMembership,
                    ClassMembership.teaching_class_id == TeachingClass.id,
                )
                .where(
                    TeachingClass.public_id == public_class_id,
                    ClassMembership.student_id == user_id,
                )
            ).first()
            if row is None:
                return None
            _teaching_class, membership = row
            return {"role": membership.role}

    def export_student_context(self, public_class_id: str, user_id: int) -> dict:
        with self._session_factory() as session:
            teaching_class = self._class(session, public_class_id)
            interactions = session.scalars(
                select(LearningInteraction)
                .where(
                    LearningInteraction.teaching_class_id == teaching_class.id,
                    LearningInteraction.user_id == user_id,
                )
                .order_by(LearningInteraction.created_at, LearningInteraction.id)
            ).all()
            evidence = session.scalars(
                select(LearningEvidence)
                .where(
                    LearningEvidence.teaching_class_id == teaching_class.id,
                    LearningEvidence.user_id == user_id,
                )
                .order_by(LearningEvidence.created_at, LearningEvidence.id)
            ).all()
            evidence_ids = [row.id for row in evidence]
            links = session.scalars(
                select(LearningEvidenceNode).where(
                    LearningEvidenceNode.evidence_id.in_(evidence_ids)
                )
            ).all() if evidence_ids else []
            feedback = session.scalars(
                select(LearningEvidenceFeedback).where(
                    LearningEvidenceFeedback.evidence_id.in_(evidence_ids)
                )
            ).all() if evidence_ids else []
            models = session.scalars(
                select(StudentNodeModel).where(
                    StudentNodeModel.teaching_class_id == teaching_class.id,
                    StudentNodeModel.user_id == user_id,
                )
            ).all()
            summaries = session.scalars(
                select(LearningContextSummary).where(
                    LearningContextSummary.teaching_class_id == teaching_class.id,
                    LearningContextSummary.user_id == user_id,
                )
            ).all()
            return {
                "schemaVersion": 1,
                "exportedAt": _iso(utc_now()),
                "classId": public_class_id,
                "userId": user_id,
                "interactions": [self._interaction_export(row) for row in interactions],
                "evidence": [self._evidence_export(row) for row in evidence],
                "evidenceNodes": [self._link_export(row) for row in links],
                "feedback": [self._feedback_export(row) for row in feedback],
                "nodeModels": [self._model_export(row) for row in models],
                "summaries": [self._summary_export(row) for row in summaries],
            }

    def delete_student_context(self, public_class_id: str, user_id: int) -> dict:
        with self._session_factory() as session:
            teaching_class = self._class(session, public_class_id)
            interaction_count = session.scalar(
                select(func.count())
                .select_from(LearningInteraction)
                .where(
                    LearningInteraction.teaching_class_id == teaching_class.id,
                    LearningInteraction.user_id == user_id,
                )
            )
            evidence_count = session.scalar(
                select(func.count())
                .select_from(LearningEvidence)
                .where(
                    LearningEvidence.teaching_class_id == teaching_class.id,
                    LearningEvidence.user_id == user_id,
                )
            )
            model_count = session.scalar(
                select(func.count())
                .select_from(StudentNodeModel)
                .where(
                    StudentNodeModel.teaching_class_id == teaching_class.id,
                    StudentNodeModel.user_id == user_id,
                )
            )
            summary_count = session.scalar(
                select(func.count())
                .select_from(LearningContextSummary)
                .where(
                    LearningContextSummary.teaching_class_id == teaching_class.id,
                    LearningContextSummary.user_id == user_id,
                )
            )
            evidence_ids = list(
                session.scalars(
                    select(LearningEvidence.id).where(
                        LearningEvidence.teaching_class_id == teaching_class.id,
                        LearningEvidence.user_id == user_id,
                    )
                )
            )
            # 测试环境可能未启用 SQLite 外键；显式按依赖顺序删除以保持与 MySQL 一致。
            if evidence_ids:
                session.execute(
                    delete(LearningEvidenceFeedback).where(
                        LearningEvidenceFeedback.evidence_id.in_(evidence_ids)
                    )
                )
                session.execute(
                    delete(LearningEvidenceNode).where(
                        LearningEvidenceNode.evidence_id.in_(evidence_ids)
                    )
                )
                session.execute(
                    delete(LearningEvidence).where(
                        LearningEvidence.id.in_(evidence_ids)
                    )
                )
            session.execute(
                delete(LearningInteraction).where(
                    LearningInteraction.teaching_class_id == teaching_class.id,
                    LearningInteraction.user_id == user_id,
                )
            )
            session.execute(
                delete(StudentNodeModel).where(
                    StudentNodeModel.teaching_class_id == teaching_class.id,
                    StudentNodeModel.user_id == user_id,
                )
            )
            session.execute(
                delete(LearningContextSummary).where(
                    LearningContextSummary.teaching_class_id == teaching_class.id,
                    LearningContextSummary.user_id == user_id,
                )
            )
            return {
                "deletedInteractions": int(interaction_count or 0),
                "deletedEvidence": int(evidence_count or 0),
                "deletedNodeModels": int(model_count or 0),
                "deletedSummaries": int(summary_count or 0),
            }

    @staticmethod
    def _class(session, public_id: str) -> TeachingClass:
        row = session.scalar(
            select(TeachingClass).where(TeachingClass.public_id == public_id)
        )
        if row is None:
            raise LookupError("class not found")
        return row

    @staticmethod
    def _context_version(session, teaching_class_id: int, user_id: int) -> int:
        value = session.scalar(
            select(func.max(LearningInteraction.context_version)).where(
                LearningInteraction.teaching_class_id == teaching_class_id,
                LearningInteraction.user_id == user_id,
            )
        )
        return int(value or 0)

    @staticmethod
    def _refresh_model(session, class_id: int, user_id: int, canonical_id: str) -> dict:
        linked = session.execute(
            select(LearningEvidence, LearningEvidenceNode)
            .join(
                LearningEvidenceNode,
                LearningEvidenceNode.evidence_id == LearningEvidence.id,
            )
            .where(
                LearningEvidence.user_id == user_id,
                LearningEvidence.teaching_class_id == class_id,
                LearningEvidenceNode.canonical_node_id == canonical_id,
            )
        ).all()
        active = [row for row, _link in linked if row.status in {"open", "confirmed"}]
        risks = [row for row in active if row.kind in RISK_KINDS and row.status == "open"]
        understood = [row for row in active if row.kind == "understanding" and row.status == "confirmed"]
        mastery = "needs_review" if risks else "mastered" if understood else "learning" if active else "unknown"
        model = session.get(StudentNodeModel, (class_id, user_id, canonical_id))
        if model is None:
            model = StudentNodeModel(
                teaching_class_id=class_id,
                user_id=user_id,
                canonical_node_id=canonical_id,
                mastery_state=mastery,
                direct_summary_json={},
                risk_summary_json={},
                open_evidence_count=0,
                version=0,
            )
            session.add(model)
        model.mastery_state = mastery
        model.direct_summary_json = {
            "claims": [row.claim for row in active if row.kind not in RISK_KINDS][:12]
        }
        model.risk_summary_json = {"claims": [row.claim for row in risks][:12]}
        model.open_evidence_count = sum(row.status == "open" for row in active)
        model.version += 1
        model.updated_at = utc_now()
        session.flush()
        return {
            "canonicalNodeId": canonical_id,
            "masteryState": mastery,
            "openEvidenceCount": model.open_evidence_count,
            "version": model.version,
        }

    @staticmethod
    def _refresh_course_summary(session, class_id: int, public_id: str, user_id: int) -> None:
        models = session.scalars(
            select(StudentNodeModel).where(
                StudentNodeModel.teaching_class_id == class_id,
                StudentNodeModel.user_id == user_id,
            )
        ).all()
        row = session.get(
            LearningContextSummary, (class_id, user_id, "course", public_id)
        )
        if row is None:
            row = LearningContextSummary(
                teaching_class_id=class_id,
                user_id=user_id,
                scope_type="course",
                scope_id=public_id,
                summary_json={},
                source_watermark="0",
                schema_version=1,
                prompt_version="student-context-v1",
                token_count=0,
            )
            session.add(row)
        summary = {
            "nodeCount": len(models),
            "needsReviewCount": sum(item.mastery_state == "needs_review" for item in models),
            "masteredCount": sum(item.mastery_state == "mastered" for item in models),
        }
        row.summary_json = summary
        row.source_watermark = str(max((item.version for item in models), default=0))
        row.token_count = _estimate_tokens(summary)
        row.updated_at = utc_now()

    @staticmethod
    def _model_payload(row: StudentNodeModel, node_id: int, title: str) -> dict:
        return {
            "nodeId": node_id,
            "title": title,
            "masteryState": row.mastery_state,
            "directSummary": deepcopy(row.direct_summary_json or {}),
            "riskSummary": deepcopy(row.risk_summary_json or {}),
            "openEvidenceCount": row.open_evidence_count,
            "version": row.version,
            "updatedAt": _iso(row.updated_at),
        }

    @staticmethod
    def _evidence_payload(
        evidence: LearningEvidence, link: LearningEvidenceNode, *, node_id: int
    ) -> dict:
        return {
            "id": evidence.id,
            "kind": evidence.kind,
            "claim": evidence.claim,
            "status": evidence.status,
            "confidence": float(evidence.confidence),
            "severity": evidence.severity,
            "sourceType": evidence.source_type,
            "excerpt": evidence.evidence_excerpt,
            "updatedAt": _iso(evidence.updated_at),
            "relationRole": link.relation_role,
            "relationWeight": float(link.weight),
            "relationPath": deepcopy(link.relation_path_json or {}),
            "nodeId": node_id,
        }

    @staticmethod
    def _interaction_export(row: LearningInteraction) -> dict:
        return {
            "id": row.id,
            "client_interaction_id": row.client_interaction_id,
            "assignment_id": row.assignment_id,
            "snapshot_id": row.snapshot_id,
            "canonical_node_id": row.canonical_node_id,
            "node_id": row.node_id,
            "action": row.action,
            "user_proof": row.user_proof,
            "assistant_response": row.assistant_response,
            "context_version": row.context_version,
            "context_snapshot": deepcopy(row.context_snapshot_json or {}),
            "classification_status": row.classification_status,
            "token_estimate": row.token_estimate,
            "result": deepcopy(row.result_json or {}),
            "created_at": _iso(row.created_at),
        }

    @staticmethod
    def _evidence_export(row: LearningEvidence) -> dict:
        return {
            "id": row.id,
            "interaction_id": row.interaction_id,
            "canonical_node_id": row.canonical_node_id,
            "kind": row.kind,
            "claim": row.claim,
            "status": row.status,
            "source_type": row.source_type,
            "confidence": row.confidence,
            "severity": row.severity,
            "evidence_excerpt": row.evidence_excerpt,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    @staticmethod
    def _link_export(row: LearningEvidenceNode) -> dict:
        return {
            "evidence_id": row.evidence_id,
            "canonical_node_id": row.canonical_node_id,
            "relation_role": row.relation_role,
            "relation_path": deepcopy(row.relation_path_json or {}),
            "weight": row.weight,
        }

    @staticmethod
    def _feedback_export(row: LearningEvidenceFeedback) -> dict:
        return {
            "id": row.id,
            "evidence_id": row.evidence_id,
            "action": row.action,
            "previous_status": row.previous_status,
            "new_status": row.new_status,
            "note": row.note,
            "created_at": _iso(row.created_at),
        }

    @staticmethod
    def _model_export(row: StudentNodeModel) -> dict:
        return {
            "canonical_node_id": row.canonical_node_id,
            "mastery_state": row.mastery_state,
            "direct_summary": deepcopy(row.direct_summary_json or {}),
            "risk_summary": deepcopy(row.risk_summary_json or {}),
            "open_evidence_count": row.open_evidence_count,
            "version": row.version,
            "updated_at": _iso(row.updated_at),
        }

    @staticmethod
    def _summary_export(row: LearningContextSummary) -> dict:
        return {
            "scope_type": row.scope_type,
            "scope_id": row.scope_id,
            "summary": deepcopy(row.summary_json or {}),
            "source_watermark": row.source_watermark,
            "schema_version": row.schema_version,
            "prompt_version": row.prompt_version,
            "token_count": row.token_count,
            "updated_at": _iso(row.updated_at),
        }
