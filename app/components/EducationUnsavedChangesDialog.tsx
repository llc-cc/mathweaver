import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, ArrowLeft } from "lucide-react";
import "../routes/education.css";

interface EducationUnsavedChangesDialogProps {
  open: boolean;
  theme: "light" | "dark";
  onCancel: () => void;
  onConfirm: () => void;
}

export function EducationUnsavedChangesDialog({ open, theme, onCancel, onConfirm }: EducationUnsavedChangesDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const continueButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    continueButtonRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onCancel();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onCancel, open]);

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div
      className="edu-root edu-modal-backdrop edu-unsaved-changes-backdrop"
      data-theme={theme}
      role="presentation"
      onMouseDown={event => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <section
        className="edu-confirm-modal edu-unsaved-changes-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        onMouseDown={event => event.stopPropagation()}
      >
        <div className="edu-unsaved-changes-icon" aria-hidden="true"><AlertTriangle size={22} /></div>
        <span className="edu-kicker">未保存修改</span>
        <h2 id={titleId}>要离开当前作业吗？</h2>
        <p id={descriptionId}>当前作业还有尚未保存的修改。放弃后，本次编辑内容将不会保留。</p>
        <div className="edu-confirm-actions edu-unsaved-changes-actions">
          <button ref={continueButtonRef} type="button" className="edu-button ghost" onClick={onCancel}>继续编辑</button>
          <button type="button" className="edu-button danger" onClick={onConfirm}><ArrowLeft size={14} />放弃修改并返回</button>
        </div>
      </section>
    </div>,
    document.body,
  );
}
