import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Flame, Gem, Sparkles, Zap } from "lucide-react";
import {
  describeEducationCurrencyChest,
  type EducationCurrencyChest,
  type EducationCurrencyChestPresentation,
} from "./education-game";
import "./EducationCurrencyChestCelebration.css";

type CurrencyChestPhase = "closed" | "opening" | "revealed";

interface EducationCurrencyChestCelebrationProps {
  chests: EducationCurrencyChest[];
  onAcknowledge: (chestId: string) => Promise<boolean>;
}

function RewardIcon({ presentation }: { presentation: EducationCurrencyChestPresentation }) {
  if (presentation.rewardKind === "revive_card") return <Flame size={34} />;
  if (presentation.rewardKind === "xp_card") return <Zap size={34} />;
  if (presentation.rewardKind === "gems") return <Gem size={34} />;
  return <Sparkles size={34} />;
}

export function EducationCurrencyChestCelebration({ chests, onAcknowledge }: EducationCurrencyChestCelebrationProps) {
  const [dismissedIds, setDismissedIds] = useState<string[]>([]);
  const [phase, setPhase] = useState<CurrencyChestPhase>("closed");
  const [accepting, setAccepting] = useState(false);
  const [error, setError] = useState("");
  const openingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const acceptRef = useRef<HTMLButtonElement>(null);
  const pendingChests = chests.filter(chest => !dismissedIds.includes(chest.id));
  const activeChest = pendingChests[0] ?? null;
  const presentation = activeChest ? describeEducationCurrencyChest(activeChest) : null;

  useEffect(() => {
    if (openingTimerRef.current) clearTimeout(openingTimerRef.current);
    openingTimerRef.current = null;
    setPhase("closed");
    setAccepting(false);
    setError("");
    if (activeChest) window.requestAnimationFrame(() => triggerRef.current?.focus());
    return () => {
      if (openingTimerRef.current) clearTimeout(openingTimerRef.current);
    };
  }, [activeChest?.id]);

  useEffect(() => {
    if (phase === "revealed") window.requestAnimationFrame(() => acceptRef.current?.focus());
  }, [phase]);

  useEffect(() => {
    if (!activeChest || phase !== "revealed") return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || accepting) return;
      event.preventDefault();
      acceptRef.current?.click();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [accepting, activeChest, phase]);

  if (!activeChest || !presentation || typeof document === "undefined") return null;

  const openChest = () => {
    if (phase !== "closed") return;
    setError("");
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      setPhase("revealed");
      return;
    }
    setPhase("opening");
    openingTimerRef.current = setTimeout(() => {
      openingTimerRef.current = null;
      setPhase("revealed");
    }, 1450);
  };

  const acceptReward = async () => {
    if (phase !== "revealed" || accepting) return;
    const chestId = activeChest.id;
    setAccepting(true);
    setError("");
    const acknowledged = await onAcknowledge(chestId);
    if (!acknowledged) {
      setAccepting(false);
      setError("奖励已经保存，但暂时无法确认展示状态，请重试。");
      return;
    }
    setDismissedIds(current => current.includes(chestId) ? current : [...current, chestId]);
  };

  return createPortal(
    <div className={`edu-currency-chest-backdrop ${presentation.jackpot ? "jackpot" : ""}`}>
      <section
        className={`edu-currency-chest-celebration phase-${phase}`}
        role="dialog"
        aria-modal="true"
        aria-label={`开启${presentation.title}`}
        aria-describedby="edu-currency-chest-instruction"
      >
        <div className="edu-currency-chest-heading">
          <span>{presentation.jackpot ? "稀有奖励" : "获得一个宝石箱"}</span>
          <h2>{presentation.title}</h2>
          <p id="edu-currency-chest-instruction">
            {phase === "closed" ? "开启宝箱，有概率获得惊喜大奖。" : phase === "opening" ? "宝箱正在开启……" : "奖励结果已保存，不会因刷新重新抽取。"}
          </p>
        </div>

        <button
          ref={triggerRef}
          type="button"
          className="edu-currency-chest-trigger"
          onClick={openChest}
          disabled={phase !== "closed"}
          aria-label={phase === "closed" ? `开启${presentation.title}` : `${presentation.title}正在开启`}
        >
          <span className="edu-currency-chest-burst" aria-hidden="true">
            {Array.from({ length: 10 }, (_, index) => <i key={index} />)}
          </span>
          <span className="edu-currency-chest-glow" aria-hidden="true" />
          <span className="edu-currency-chest-object" aria-hidden="true">
            <span className="edu-currency-chest-lid"><i /><b /></span>
            <span className="edu-currency-chest-body"><i /><b /><em><Gem size={24} /></em></span>
          </span>
        </button>

        <div className="edu-currency-chest-phase-copy" aria-live="polite">
          {phase === "closed" && <strong>点击开启宝箱</strong>}
          {phase === "opening" && <strong>正在解锁奖励…</strong>}
          {phase === "revealed" && (
            <div className="edu-currency-chest-result">
              <span className="edu-currency-chest-result-icon"><RewardIcon presentation={presentation} /></span>
              <small>{presentation.jackpot ? "稀有奖励！" : "你获得了"}</small>
              <strong>{presentation.rewardLabel}</strong>
              <p>{presentation.destinationLabel}</p>
              {error && <p className="edu-currency-chest-error" role="alert">{error}</p>}
              <button ref={acceptRef} type="button" className="edu-button primary" disabled={accepting} onClick={() => void acceptReward()}>
                {accepting ? "正在收下…" : pendingChests.length > 1 ? "收下并开启下一个" : "收下奖励"}
              </button>
            </div>
          )}
        </div>
        {pendingChests.length > 1 && <small className="edu-currency-chest-remaining">本次还有 {pendingChests.length - 1} 个宝箱等待开启</small>}
      </section>
    </div>,
    document.body,
  );
}
