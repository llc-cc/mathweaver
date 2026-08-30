export interface DirectQuestionDraft {
  id: string;
  nodeId?: number;
  order: number;
  question: string;
  focus: string;
  kind: string;
  referenceAnswer: string;
  expectedPoints: string[];
  maxScore: number;
}

export interface DirectAssignmentProjection {
  directStructureVersion?: number;
  targetNodeId: number;
  path: { steps: Array<{ nodeId: number; order: number }> };
  assessments: Array<{
    nodeId: number;
    questions?: Array<{
      id: string;
      nodeId: number;
      order: number;
      question: string;
      focus?: string;
      kind?: string;
      referenceAnswer?: string;
      expectedPoints?: string[];
      maxScore?: number;
    }>;
  }>;
}

/** Flatten both the per-node and legacy single-container direct structures. */
export function directQuestionDraftsForAssignment(assignment: DirectAssignmentProjection): DirectQuestionDraft[] {
  if (assignment.directStructureVersion === 1) {
    return assignment.path.steps
      .slice()
      .sort((left, right) => left.order - right.order)
      .map((step, index) => {
        const question = assignment.assessments.find(item => item.nodeId === step.nodeId)?.questions?.[0];
        return {
          id: question?.id || `direct-question-${step.nodeId}`,
          nodeId: step.nodeId,
          order: index + 1,
          question: question?.question || "",
          focus: question?.focus || "",
          kind: question?.kind && question.kind !== "direct" ? question.kind : `第 ${index + 1} 题`,
          referenceAnswer: question?.referenceAnswer || "",
          expectedPoints: question?.expectedPoints || [],
          maxScore: Number(question?.maxScore || 0),
        };
      });
  }
  const assessment = assignment.assessments.find(item => item.nodeId === assignment.targetNodeId) || assignment.assessments[0];
  return (assessment?.questions || []).map(question => ({
    id: question.id,
    nodeId: assignment.targetNodeId,
    order: question.order,
    question: question.question,
    focus: question.focus || "",
    kind: question.kind && question.kind !== "direct" ? question.kind : `第 ${question.order} 题`,
    referenceAnswer: question.referenceAnswer || "",
    expectedPoints: question.expectedPoints || [],
    maxScore: Number(question.maxScore || 0),
  }));
}

const QUESTION_START = /^\s*(?:(?:第\s*[0-9０-９一二三四五六七八九十百千]+\s*[题問])|(?:题目|问题)\s*[0-9０-９一二三四五六七八九十百千]+|(?:[0-9０-９]{1,3}|[（(][0-9０-９一二三四五六七八九十百千]+[）)])\s*[.、:：)]?)(?:\s*[.、:：)）-]\s*|\s+|$)/
const MARKDOWN_HEADING = /^\s{0,3}#{1,6}\s+\S/;

/** Split imported OCR/text content without changing the source wording. */
export function splitDirectQuestions(source: string): string[] {
  const normalized = source.replace(/\r\n?/g, "\n").trim();
  if (!normalized) return [];
  const lines = normalized.split("\n");
  const blocks: string[] = [];
  let current: string[] = [];
  const flush = () => {
    const value = current.join("\n").trim();
    if (value) blocks.push(value);
    current = [];
  };
  lines.forEach((line) => {
    const isQuestionStart = QUESTION_START.test(line) || MARKDOWN_HEADING.test(line);
    if (isQuestionStart && current.some(item => item.trim())) flush();
    current.push(line);
  });
  flush();
  return blocks.length > 1 ? blocks : [normalized];
}

export function equalDirectQuestionScores(count: number): number[] {
  if (count <= 0) return [];
  const base = Math.round((100 / count) * 10) / 10;
  const scores = Array.from({ length: count }, () => base);
  scores[scores.length - 1] = Math.round((100 - scores.slice(0, -1).reduce((sum, score) => sum + score, 0)) * 10) / 10;
  return scores;
}

export function createDirectQuestionDrafts(source: string): DirectQuestionDraft[] {
  const questions = splitDirectQuestions(source);
  const scores = equalDirectQuestionScores(questions.length);
  return questions.map((question, index) => ({
    id: `direct-question-${Date.now()}-${index}`,
    order: index + 1,
    question,
    focus: "",
    kind: `第 ${index + 1} 题`,
    referenceAnswer: "",
    expectedPoints: [],
    maxScore: scores[index] || 0,
  }));
}

export function rebalanceDirectQuestionDrafts(questions: DirectQuestionDraft[]): DirectQuestionDraft[] {
  const scores = equalDirectQuestionScores(questions.length);
  return questions.map((question, index) => ({ ...question, order: index + 1, maxScore: scores[index] || 0 }));
}

/** Keep teacher-entered score proportions while projecting the draft total to 100. */
export function normalizeDirectQuestionDraftScores(questions: DirectQuestionDraft[]): DirectQuestionDraft[] {
  if (questions.length === 0) return [];
  const scores = questions.map(question => Number(question.maxScore));
  if (scores.some(score => !Number.isFinite(score) || score <= 0)) return questions;
  const total = scores.reduce((sum, score) => sum + score, 0);
  if (total <= 0) return questions;
  const normalized = scores.map(score => Math.round((score / total) * 100 * 10) / 10);
  normalized[normalized.length - 1] = Math.round((100 - normalized.slice(0, -1).reduce((sum, score) => sum + score, 0)) * 10) / 10;
  return questions.map((question, index) => ({ ...question, maxScore: normalized[index] || 0 }));
}
