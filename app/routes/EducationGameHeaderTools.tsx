import { useEffect, useRef, useState } from "react";
import { Check, Flame, Gem, Gift, Loader2, ShoppingBag, Sparkles, X } from "lucide-react";
import { parseEducationShopItems, type EducationCheckinDay, type EducationGameSummary, type EducationShopItem } from "./education-game";

interface EducationGameHeaderToolsProps {
  summary: EducationGameSummary;
  onGameAction: (path: string, init?: RequestInit) => Promise<unknown>;
}

type PopoverKind = "checkin" | "gems" | "shop";

function isCoarsePointer() {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(hover: none), (pointer: coarse)").matches;
}

function actionError(cause: unknown) {
  return cause instanceof Error ? cause.message : "操作未完成，请稍后再试。";
}

function shopItemIcon(item: EducationShopItem) {
  if (item.itemKey === "revive_card") return <Flame size={19} />;
  if (item.itemKey === "xp_card") return <Sparkles size={19} />;
  return <Gift size={19} />;
}

function shopItemType(item: EducationShopItem) {
  return item.kind === "system" ? "系统道具" : "课程奖励";
}

function checkinDayLabel(day: EducationCheckinDay, index: number) {
  const labels = ["一", "二", "三", "四", "五", "六", "日"];
  return <>{labels[index] ?? "—"}<small>{day.date.slice(8, 10)}</small></>;
}

function checkinDayState(day: EducationCheckinDay) {
  if (day.paused) return { className: "paused", label: "课程暂停" };
  if (day.kind === "genuine") return { className: "checked", label: "已签到" };
  if (day.kind === "revived") return { className: "revived", label: "已复燃" };
  return { className: "empty", label: "未签到" };
}

export function EducationGameHeaderTools({ summary, onGameAction }: EducationGameHeaderToolsProps) {
  const checkin = summary.checkin;
  const wallet = summary.wallet;
  const inventory = summary.inventory;
  const [popover, setPopover] = useState<PopoverKind | null>(null);
  const [shopOpen, setShopOpen] = useState(false);
  const [shopItems, setShopItems] = useState<EducationShopItem[]>([]);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const checkinRef = useRef<HTMLDivElement>(null);
  const gemsRef = useRef<HTMLDivElement>(null);
  const shopRef = useRef<HTMLDivElement>(null);
  const closeTimerRef = useRef<number | null>(null);

  const clearCloseTimer = () => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  };

  const openPopover = (kind: PopoverKind) => {
    clearCloseTimer();
    setPopover(kind);
    setMessage("");
  };

  const schedulePopoverClose = () => {
    clearCloseTimer();
    closeTimerRef.current = window.setTimeout(() => setPopover(null), 150);
  };

  useEffect(() => () => clearCloseTimer(), []);

  useEffect(() => {
    if (!popover && !shopOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && (checkinRef.current?.contains(target) || gemsRef.current?.contains(target) || shopRef.current?.contains(target))) return;
      setPopover(null);
      if (shopOpen && !(target instanceof Element && target.closest(".edu-shop-dialog"))) setShopOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPopover(null);
        setShopOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [popover, shopOpen]);

  const runAction = async (label: string, path: string, init?: RequestInit) => {
    setBusy(label);
    setMessage("");
    try {
      return await onGameAction(path, init);
    } catch (cause) {
      setMessage(actionError(cause));
      return null;
    } finally {
      setBusy("");
    }
  };

  const loadShopItems = async () => {
    const result = await runAction("shop", "/shop");
    if (result) setShopItems(parseEducationShopItems(result));
  };

  const openShop = () => {
    setPopover(null);
    setShopItems([]);
    setShopOpen(true);
    void loadShopItems();
  };

  const redeem = async (item: EducationShopItem) => {
    const result = await runAction("redeem", "/shop/items/" + encodeURIComponent(item.id) + "/redeem", { method: "POST" });
    if (result) await loadShopItems();
  };

  const handleCheckinClick = () => {
    if (isCoarsePointer()) {
      openPopover("checkin");
      return;
    }
    if (!checkin?.todayCheckedIn) void runAction("checkin", "/checkins", { method: "POST" });
  };

  const confirmCheckin = () => {
    if (!checkin?.todayCheckedIn) void runAction("checkin", "/checkins", { method: "POST" });
  };

  const handleGemsClick = () => {
    if (isCoarsePointer()) {
      if (popover === "gems") setPopover(null);
      else openPopover("gems");
    }
  };

  const systemItems = shopItems.filter(item => item.kind === "system");
  const customItems = shopItems.filter(item => item.kind !== "system");
  const weekDays = checkin?.weekDays ?? [];

  return (
    <div className="edu-game-header-tools" aria-label="课程签到与宝石工具">
      <div
        ref={checkinRef}
        className={`edu-game-header-tool edu-game-header-checkin${popover === "checkin" ? " open" : ""}`}
        onMouseEnter={() => openPopover("checkin")}
        onMouseLeave={schedulePopoverClose}
        onFocusCapture={() => openPopover("checkin")}
        onBlurCapture={event => {
          if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return;
          schedulePopoverClose();
        }}
      >
        <button
          type="button"
          className={`edu-game-tool-trigger edu-adventure-checkin${checkin?.todayCheckedIn ? " done" : ""}`}
          aria-haspopup="dialog"
          aria-expanded={popover === "checkin"}
          aria-label={checkin?.todayCheckedIn ? `今日已签到，连续 ${checkin.streakDays} 天` : "签到，获得 5 XP"}
          onClick={handleCheckinClick}
        >
          <Flame size={16} />
          <span>{checkin?.todayCheckedIn ? `今日已签到 · ${checkin.streakDays} 天` : <>签到 <small>+5 XP</small></>}</span>
        </button>
        {popover === "checkin" && <div className="edu-game-popover edu-checkin-popover" role="dialog" aria-label="签到日历">
          <div className="edu-game-popover-heading"><span className="edu-popover-icon checkin"><Flame size={19} /></span><div><strong>{checkin?.streakDays ?? 0} 天连续火花</strong><small>{checkin?.todayCheckedIn ? "今天已完成签到，继续保持" : "今天签到，延续你的学习火花"}</small></div></div>
          <div className="edu-checkin-week" aria-label="本周签到日历">
            {weekDays.map((day, index) => {
              const state = checkinDayState(day);
              return <div className={`edu-checkin-day ${state.className}${day.isToday ? " today" : ""}`} key={day.date} aria-label={`${day.date}，${state.label}`}><span>{checkinDayLabel(day, index)}</span><i>{day.kind === "genuine" ? <Check size={14} /> : day.kind === "revived" ? <Sparkles size={13} /> : day.paused ? "—" : ""}</i></div>;
            })}
          </div>
          <div className="edu-checkin-summary"><span>本周真实签到 <b>{checkin?.weeklyGenuineDays ?? 0}/5</b></span><span>累计签到 <b>{checkin?.totalGenuineDays ?? 0} 天</b></span></div>
          {!checkin?.todayCheckedIn && <button type="button" className="edu-button primary edu-checkin-action" disabled={Boolean(busy)} onClick={confirmCheckin}>{busy === "checkin" ? <><Loader2 className="edu-spin" size={14} />签到中…</> : <><Flame size={14} />立即签到 · +5 XP</>}</button>}
          {checkin?.canReviveYesterday && <button type="button" className="edu-button secondary edu-revive-action" disabled={Boolean(busy)} onClick={() => void runAction("revive", "/checkins/revive", { method: "POST" })}><Sparkles size={14} />使用复燃卡（剩余 {checkin.reviveCards}）</button>}
          {message && <p className="edu-game-popover-message" role="status">{message}</p>}
        </div>}
      </div>

      <div
        ref={gemsRef}
        className={`edu-game-header-tool edu-game-header-gems${popover === "gems" ? " open" : ""}`}
        onMouseEnter={() => openPopover("gems")}
        onMouseLeave={schedulePopoverClose}
        onFocusCapture={() => openPopover("gems")}
        onBlurCapture={event => {
          if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return;
          schedulePopoverClose();
        }}
      >
        <button type="button" className="edu-game-tool-trigger edu-adventure-gem-button" aria-haspopup="dialog" aria-expanded={popover === "gems"} aria-label={`当前课程宝石余额 ${wallet?.balance ?? 0}`} onClick={handleGemsClick}><Gem size={16} /><strong>{wallet?.balance ?? 0}</strong><span>宝石</span></button>
        {popover === "gems" && <div className="edu-game-popover edu-gems-popover" role="dialog" aria-label="宝石余额"><div className="edu-gems-balance"><Gem size={25} /><strong>{wallet?.balance ?? 0}</strong><span>宝石</span></div><p>宝石只在当前课程内使用。</p></div>}
      </div>

      <div
        ref={shopRef}
        className={`edu-game-header-tool edu-game-header-shop${popover === "shop" ? " open" : ""}`}
        onMouseEnter={() => openPopover("shop")}
        onMouseLeave={schedulePopoverClose}
        onFocusCapture={() => openPopover("shop")}
        onBlurCapture={event => {
          if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return;
          schedulePopoverClose();
        }}
      >
        <button type="button" className="edu-game-tool-trigger edu-adventure-shop-button" aria-haspopup="dialog" aria-expanded={popover === "shop"} aria-label="打开宝石小店" onClick={openShop}><ShoppingBag size={16} />宝石小店</button>
        {popover === "shop" && <div className="edu-game-popover edu-shop-popover" role="dialog" aria-label="宝石小店说明">
          <div className="edu-game-popover-heading"><span className="edu-popover-icon shop"><ShoppingBag size={17} /></span><div><strong>宝石小店</strong><small>兑换课程学习奖励</small></div></div>
          <p>使用课程宝石兑换复燃卡、经验卡和教师设置的课程奖励。</p>
          <small>仅限当前课程使用；点击进入小店。</small>
        </div>}
      </div>

      {shopOpen && <div className="edu-game-dialog-backdrop" role="presentation" onMouseDown={() => setShopOpen(false)}><section className="edu-game-dialog edu-shop-dialog" role="dialog" aria-modal="true" aria-label="宝石小店" onMouseDown={event => event.stopPropagation()}>
        <header><div><span className="edu-kicker">课程内可消费货币</span><h2><ShoppingBag size={20} />宝石小店 · {wallet?.balance ?? 0}</h2><p>宝石只在当前课程使用；兑换不会影响宝石榜的累计获得数。</p></div><button type="button" aria-label="关闭小店" onClick={() => setShopOpen(false)}><X size={18} /></button></header>
        <div className="edu-inventory-strip"><span>复燃卡 {inventory?.reviveCard ?? 0}</span><span>经验卡 {inventory?.xpCard ?? 0}</span>{(inventory?.xpCard ?? 0) > 0 && <button type="button" disabled={Boolean(busy)} onClick={() => void runAction("activate-card", "/inventory/xp-card/activate", { method: "POST" })}>激活经验卡</button>}</div>
        {busy === "shop" && !shopItems.length && <div className="edu-shop-loading"><Loader2 className="edu-spin" size={16} />正在加载课程商品…</div>}
        {!busy && !shopItems.length && <p className="edu-shop-empty">当前暂无可兑换商品。</p>}
        {systemItems.length > 0 && <section className="edu-shop-group"><h3>系统道具 <span>{systemItems.length}</span></h3><div className="edu-shop-items">{systemItems.map(item => <ShopItemCard key={item.id} item={item} walletBalance={wallet?.balance ?? 0} busy={busy} onRedeem={redeem} />)}</div></section>}
        {customItems.length > 0 && <section className="edu-shop-group"><h3>课程奖励 <span>{customItems.length}</span></h3><div className="edu-shop-items">{customItems.map(item => <ShopItemCard key={item.id} item={item} walletBalance={wallet?.balance ?? 0} busy={busy} onRedeem={redeem} />)}</div></section>}
        {message && <p className="edu-game-dialog-message" role="status">{message}</p>}
      </section></div>}
    </div>
  );
}

function ShopItemCard({ item, walletBalance, busy, onRedeem }: { item: EducationShopItem; walletBalance: number; busy: string; onRedeem: (item: EducationShopItem) => Promise<void> }) {
  const soldOut = item.stock === 0;
  const insufficient = walletBalance < item.gemPrice;
  return <article className={`edu-shop-item-card ${item.kind === "system" ? "system" : "custom"}`}>
    <div className="edu-shop-item-icon">{shopItemIcon(item)}</div>
    <div className="edu-shop-item-body"><span className="edu-shop-item-kind">{shopItemType(item)}</span><strong>{item.title}</strong><p>{item.description}</p><small>{item.stock === null ? "不限库存" : soldOut ? "暂时售罄" : `剩余 ${item.stock}`}</small></div>
    <div className="edu-shop-item-footer"><b><Gem size={14} />{item.gemPrice}</b><button type="button" disabled={Boolean(busy) || insufficient || soldOut} onClick={() => void onRedeem(item)}>{busy === "redeem" ? <Loader2 className="edu-spin" size={13} /> : soldOut ? "已售罄" : insufficient ? "宝石不足" : "兑换"}</button></div>
  </article>;
}
