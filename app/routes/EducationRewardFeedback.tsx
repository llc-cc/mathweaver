import { ArrowRight, CheckCircle2, Sparkles, Trophy, X } from "lucide-react";
import { isRenderableEducationReward, type EducationRewardReceipt } from "./education-game";
import "./EducationRewardFeedback.css";

export interface EducationRewardFeedbackProps {
  reward: EducationRewardReceipt | null;
  nextAction?: { label: string; onClick: () => void };
  onClose: () => void;
}

export function EducationRewardFeedback({ reward, nextAction, onClose }: EducationRewardFeedbackProps) {
  if (!isRenderableEducationReward(reward)) return null;
  const weeklyProgress = reward.weeklyGoal > 0
    ? Math.min(100, Math.round((reward.weeklyXp / reward.weeklyGoal) * 100))
    : 0;
  const levelProgress = reward.nextLevelXp > 0
    ? Math.min(100, Math.round((reward.levelXp / reward.nextLevelXp) * 100))
    : 0;

  return (
    <aside className="edu-reward-feedback" role="status" aria-live="polite" aria-label="学习奖励">
      <div className="edu-reward-feedback-head">
        <span className="edu-reward-feedback-icon"><Sparkles size={18} /></span>
        <div>
          <strong>完成学习任务</strong>
          <span>本次获得 <b>+{reward.xpDelta} XP</b></span>
        </div>
        <button type="button" className="edu-icon-button" onClick={onClose} aria-label="关闭奖励提示"><X size={16} /></button>
      </div>
      <div className="edu-reward-feedback-progress">
        <div><span>等级 {reward.level}</span><b>{reward.levelXp} / {reward.nextLevelXp} XP</b></div>
        <div className="edu-reward-bar level"><span style={{ width: `${levelProgress}%` }} /></div>
        <div><span>本周目标</span><b>{reward.weeklyXp} / {reward.weeklyGoal} XP</b></div>
        <div className="edu-reward-bar weekly"><span style={{ width: `${weeklyProgress}%` }} /></div>
      </div>
      {reward.unlockedAchievements.length > 0 && (
        <div className="edu-reward-achievements">
          <span className="edu-reward-section-label"><Trophy size={13} />新解锁成就</span>
          {reward.unlockedAchievements.map(achievement => (
            <div className="edu-reward-achievement" key={achievement.key}>
              <CheckCircle2 size={14} />
              <span><strong>{achievement.title}</strong><small>{achievement.description}</small></span>
            </div>
          ))}
        </div>
      )}
      <div className="edu-reward-feedback-actions">
        {nextAction && <button type="button" className="edu-button primary" onClick={nextAction.onClick}>{nextAction.label}<ArrowRight size={14} /></button>}
        <button type="button" className="edu-button ghost" onClick={onClose}>稍后继续</button>
      </div>
    </aside>
  );
}
