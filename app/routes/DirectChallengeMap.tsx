import { useEffect, useRef, useState } from "react";
import { ArrowRight, BookOpen, Brain, Check, Compass, Flag, GitBranch, Pencil, Send, Sigma, Sparkles } from "lucide-react";
import type { EducationAssignment } from "./education";
import { isRenderableEducationReward, type DirectChallengeQuestionNode, type EducationRewardReceipt } from "./education-game";

export interface DirectChallengeMapProps {
  assignment: EducationAssignment;
  nodes: DirectChallengeQuestionNode[];
  activeKey: string | null;
  completionCount: number;
  recommendedKey: string | null;
  recentlyCompletedKey?: string | null;
  readyToSubmit: boolean;
  busy?: boolean;
  submitting: boolean;
  onOpenQuestion: (key: string) => void;
  onSubmit: () => void;
  registerNodeRef: (key: string, element: HTMLButtonElement | null) => void;
  registerFinishRef: (element: HTMLButtonElement | null) => void;
}
function nodeStateLabel(node: DirectChallengeQuestionNode) {
  if (node.state === "completed") return "已完成";
  if (node.state === "draft") return "作答中";
  return "未开始";
}

function QuestionNodeIcon({ node }: { node: DirectChallengeQuestionNode }) {
  if (node.state === "completed") return <Check size={22} />;
  if (node.state === "draft") return <Pencil size={19} />;
  const variant = (node.order - 1) % 4;
  if (variant === 1) return <BookOpen size={21} />;
  if (variant === 2) return <Brain size={21} />;
  if (variant === 3) return <Compass size={21} />;
  return <Sigma size={21} />;
}

export function DirectChallengeSubmitConfirm({ questionCount, onCancel, onConfirm }: {
  questionCount: number;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const confirmButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    confirmButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onCancel]);

  return (
    <div className="direct-question-completion-backdrop direct-submit-confirm-backdrop" role="presentation" onMouseDown={event => {
      if (event.target === event.currentTarget) onCancel();
    }}>
      <section className="direct-question-completion-dialog direct-submit-confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="direct-submit-confirm-title" aria-describedby="direct-submit-confirm-description" onMouseDown={event => event.stopPropagation()}>
        <div className="direct-question-completion-icon direct-submit-confirm-icon"><Send size={26} /></div>
        <span className="edu-kicker">作业提交</span>
        <h2 id="direct-submit-confirm-title">确认提交作业？</h2>
        <p id="direct-submit-confirm-description">所有题目都已完成。提交后，答案将进入教师评价。</p>
        <div className="direct-submit-confirm-status"><Check size={15} /><span>已完成全部 {questionCount} 道题</span></div>
        <div className="direct-submit-confirm-actions">
          <button type="button" className="edu-button ghost" onClick={onCancel}>取消</button>
          <button type="button" ref={confirmButtonRef} className="edu-button primary" onClick={onConfirm}><Send size={14} />确认提交</button>
        </div>
      </section>
    </div>
  );
}

export function DirectChallengeMap({
  assignment,
  nodes,
  activeKey,
  completionCount,
  recommendedKey,
  recentlyCompletedKey,
  readyToSubmit,
  busy = false,
  submitting,
  onOpenQuestion,
  onSubmit,
  registerNodeRef,
  registerFinishRef,
}: DirectChallengeMapProps) {
  const remaining = Math.max(0, nodes.length - completionCount);
  const [submitConfirmOpen, setSubmitConfirmOpen] = useState(false);

  const requestSubmit = () => {
    if (!readyToSubmit || submitting || busy) return;
    setSubmitConfirmOpen(true);
  };
  const confirmSubmit = () => {
    if (!readyToSubmit || submitting || busy) return;
    setSubmitConfirmOpen(false);
    onSubmit();
  };

  return (
    <>
      <aside className="direct-challenge-route-sidebar" aria-label="题目挑战路线">
      <header className="direct-challenge-route-sidebar-header">
        <div className="direct-challenge-summary-title"><GitBranch size={17} /><div><strong>挑战路线</strong><small>{assignment.title}</small></div></div>
        <strong className="direct-challenge-route-sidebar-count">{completionCount}<span>/ {nodes.length}</span></strong>
      </header>
      <div className="direct-challenge-route-sidebar-progress" aria-label={`已完成 ${completionCount} / ${nodes.length} 道题`}><span style={{ width: `${nodes.length ? Math.round((completionCount / nodes.length) * 100) : 0}%` }} /></div>

      <section className="direct-challenge-route" aria-label="题目节点路线">
        {nodes.map((node, index) => {
          const recommended = node.key === recommendedKey;
          const active = node.key === activeKey;
          const landmarkVariant = ((node.order - 1) % 4) + 1;
          const recommendationId = `direct-challenge-recommendation-${node.key.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
          return (
            <div className={`direct-challenge-route-stop-wrap ${index % 2 ? "offset" : ""}`} key={node.key}>
              {index > 0 && <span className="direct-challenge-route-connector" aria-hidden="true" />}
              <div className={`direct-challenge-route-stop ${node.state}${recommended ? " recommended" : ""}${active ? " active" : ""}${recentlyCompletedKey === node.key ? " just-completed" : ""}`}>
                {recommended && <span id={recommendationId} className="direct-challenge-recommendation">{node.state === "draft" ? "继续作答" : "推荐下一题"}</span>}
                <button
                  type="button"
                  ref={element => registerNodeRef(node.key, element)}
                  className="direct-challenge-route-button"
                  onClick={() => onOpenQuestion(node.key)}
                  disabled={busy}
                  aria-current={active ? "step" : undefined}
                  aria-describedby={recommended ? recommendationId : undefined}
                  aria-label={`第 ${node.order} 题，${nodeStateLabel(node)}${active ? "，当前题目" : ""}`}
                >
                  <span className={`direct-challenge-route-node ${node.state} variant-${landmarkVariant}`} aria-hidden="true">
                    <span className="direct-challenge-route-node-symbol"><QuestionNodeIcon node={node} /></span>
                    <em>{node.order}</em>
                    <i /><b />
                  </span>
                  <span className="direct-challenge-route-copy"><strong>第 {node.order} 题</strong><small>{active ? "当前题目" : `${nodeStateLabel(node)} · 点击进入答题`}</small></span>
                  <ArrowRight size={15} aria-hidden="true" />
                </button>
              </div>
            </div>
          );
        })}

        <div className={`direct-challenge-route-stop-wrap finish ${readyToSubmit ? "ready" : "locked"}`}>
          <span className="direct-challenge-route-connector" aria-hidden="true" />
          <div className="direct-challenge-route-stop">
            <button
              type="button"
              ref={registerFinishRef}
              className="direct-challenge-route-button"
              onClick={requestSubmit}
              disabled={!readyToSubmit || submitting || busy}
              aria-label={readyToSubmit ? "提交挑战" : `提交挑战，还差 ${remaining} 道题`}
            >
              <span className="direct-challenge-route-node finish" aria-hidden="true"><span className="direct-challenge-route-node-symbol"><Send size={20} /></span><i /><b /></span>
              <span className="direct-challenge-route-copy"><strong>提交挑战</strong><small>{readyToSubmit ? "所有题目已完成，可以提交" : `完成剩余 ${remaining} 道题后解锁`}</small></span>
              <Flag size={15} aria-hidden="true" />
            </button>
          </div>
        </div>
      </section>
      </aside>
      {submitConfirmOpen && <DirectChallengeSubmitConfirm questionCount={nodes.length} onCancel={() => setSubmitConfirmOpen(false)} onConfirm={confirmSubmit} />}
    </>
  );
}

export interface DirectQuestionCompletionTransitionProps {
  questionOrder: number;
  completedCount: number;
  total: number;
  reward?: EducationRewardReceipt | null;
  onContinue: () => void;
}

export function DirectQuestionCompletionTransition({ questionOrder, completedCount, total, reward, onContinue }: DirectQuestionCompletionTransitionProps) {
  const continueButtonRef = useRef<HTMLButtonElement>(null);
  const visibleReward = isRenderableEducationReward(reward);
  const progress = total > 0 ? Math.round((completedCount / total) * 100) : 0;

  useEffect(() => {
    continueButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onContinue();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onContinue]);

  return (
    <div className="direct-question-completion-backdrop" role="presentation">
      <section className="direct-question-completion-dialog" role="dialog" aria-modal="true" aria-labelledby="direct-question-completion-title" aria-describedby="direct-question-completion-description">
        <div className="direct-question-completion-icon"><Check size={28} /></div>
        <span className="edu-kicker">路线进度已更新</span>
        <h2 id="direct-question-completion-title">第 {questionOrder} 题已完成</h2>
        <p id="direct-question-completion-description">答案已保存。完成情况会在教师评价后进入正式成绩。</p>
        <div className="direct-question-completion-progress"><div><span>{completedCount} / {total} 题已完成</span><strong>{progress}%</strong></div><div className="direct-question-completion-progress-bar"><span style={{ width: `${progress}%` }} /></div></div>
        {visibleReward && <div className="direct-question-completion-reward"><Sparkles size={16} /><strong>+{reward.xpDelta} XP</strong><span>等级 {reward.level} · 本周 {reward.weeklyXp} / {reward.weeklyGoal} XP</span></div>}
        <button type="button" ref={continueButtonRef} className="edu-button primary direct-question-completion-continue" onClick={onContinue}>继续探索<ArrowRight size={15} /></button>
      </section>
    </div>
  );
}

