import React, { createContext, useContext, useRef, useState, useCallback } from "react";
import { apiUrl } from "~/api";
import { MAIN_PIPELINE_STAGE_COUNT } from "~/pipeline-stages";

// ── Types ────────────────────────────────────────────────────────────────────

export type JobPhase = "running" | "paused" | "done" | "error";
export type JobPendingAction = "pause" | "resume" | "cancel" | null;
export type JobErrorCode =
  | "api_config"
  | "service_limit"
  | "network"
  | "model_response"
  | "document_input"
  | "pipeline_stage"
  | "internal";

export interface RestoredJob {
  job_id: string;
  status: "running";
  filename: string;
  stage: string | null;
  stage_label: string | null;
  stage_index: number;
  total_stages: number;
  stages_done: string[];
  source_markdown: string;
  experimental_logic_ir?: boolean;
}

export interface BackgroundJob {
  id: string;
  filename: string;
  phase: JobPhase;
  stage: string | null;
  stageLabel: string | null;
  stagesDone: string[];
  totalStages: number;
  pct: number;
  errorCode: JobErrorCode | null;
  errorTitle: string | null;
  errorMsg: string | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  result: any | null;
  sourceMarkdown: string;
  pendingAction: JobPendingAction;
}

interface JobsCtxValue {
  jobs: Record<string, BackgroundJob>;
  latestJobId: string | null;
  startJob: (jobId: string, filename: string, sourceMarkdown: string) => void;
  pauseJob: (jobId: string) => Promise<boolean>;
  resumeJob: (jobId: string) => Promise<boolean>;
  cancelJob: (jobId: string) => Promise<boolean>;
  restoreJob: (job: RestoredJob) => void;
  dismissJob: (jobId: string) => void;
}

// ── Context ──────────────────────────────────────────────────────────────────

const JobsCtx = createContext<JobsCtxValue | null>(null);

function mutationHeaders(): HeadersInit | undefined {
  if (typeof window === "undefined") return undefined;
  const token = window.localStorage.getItem("mg_token");
  return token ? { Authorization: `Bearer ${token}` } : undefined;
}

// ── Error classification ──────────────────────────────────────────────────────

function classifyError(isNetwork: boolean, statusCode?: number): {
  errorCode: JobErrorCode;
  errorTitle: string;
  errorMsg: string;
} {
  if (isNetwork) {
    return {
      errorCode: "network",
      errorTitle: "任务状态连接已中断",
      errorMsg: "请确认后端服务和本机网络正常后重试。",
    };
  }
  if (statusCode && statusCode >= 500) {
    return {
      errorCode: "internal",
      errorTitle: "后端服务暂时异常",
      errorMsg: "请稍后重试；若仍然失败，请检查后端服务状态。",
    };
  }
  return {
    errorCode: "network",
    errorTitle: "任务状态查询失败",
    errorMsg: "请检查后端服务是否正常运行。",
  };
}

// ── Provider ─────────────────────────────────────────────────────────────────

export function JobsProvider({ children }: { children: React.ReactNode }) {
  const [jobs, setJobs] = useState<Record<string, BackgroundJob>>({});
  const [latestJobId, setLatestJobId] = useState<string | null>(null);
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const fails = useRef<Record<string, number>>({});

  const patchJob = useCallback((id: string, patch: Partial<BackgroundJob>) => {
    setJobs(prev => {
      if (!prev[id]) return prev;
      return { ...prev, [id]: { ...prev[id], ...patch } };
    });
  }, []);

  const poll = useCallback((id: string) => {
    const check = async () => {
      let statusCode: number | undefined;
      let isNetwork = false;

      try {
        const ctrl = new AbortController();
        const timeout = setTimeout(() => ctrl.abort(), 15000);
        let res: Response;
        try {
          res = await fetch(apiUrl(`/api/v2/jobs/${id}/status`), { signal: ctrl.signal });
          statusCode = res.status;
        } finally {
          clearTimeout(timeout);
        }

        if (!res.ok) {
          fails.current[id] = (fails.current[id] ?? 0) + 1;
          if (fails.current[id] >= 3) {
            patchJob(id, { phase: "error", ...classifyError(false, res.status) });
            return;
          }
          timers.current[id] = setTimeout(check, 3000);
          return;
        }

        // Success — reset failure counter
        fails.current[id] = 0;
        const status = await res.json();

        patchJob(id, {
          ...(status.status === "running" ? { phase: "running" as const } : {}),
          stage: status.stage ?? null,
          stageLabel: status.stage_label ?? null,
          stagesDone: status.stages_done ?? [],
          totalStages: status.total_stages ?? MAIN_PIPELINE_STAGE_COUNT,
          pct:
            status.total_stages
              ? Math.round(
                  (
                    status.status === "running" && status.stage
                      ? Math.max((status.stages_done ?? []).length, (status.stage_index ?? 0) + 1)
                      : (status.stages_done ?? []).length
                  ) / status.total_stages * 100
                )
              : 0,
        });

        if (status.status === "done") {
          try {
            const rRes = await fetch(apiUrl(`/api/v2/jobs/${id}/result`));
            const result = await rRes.json();
            patchJob(id, { phase: "done", result, pct: 100, pendingAction: null });
          } catch {
            patchJob(id, {
              phase: "error",
              errorCode: "internal",
              errorTitle: "处理结果获取失败",
              errorMsg: "任务已经完成，但无法读取处理结果，请稍后重试。",
            });
          }
          return;
        }

        if (status.status === "error") {
          patchJob(id, {
            phase: "error",
            errorCode: status.error_code ?? "internal",
            errorTitle: status.error_title ?? "处理过程中出现异常",
            errorMsg: status.error ?? "文档处理失败",
            pendingAction: null,
          });
          return;
        }

        if (status.status === "paused") {
          patchJob(id, { phase: "paused", pendingAction: null });
          return;
        }

        // Still running — poll again
        timers.current[id] = setTimeout(check, 2000);

      } catch {
        isNetwork = true;
        fails.current[id] = (fails.current[id] ?? 0) + 1;
        if (fails.current[id] >= 3) {
          patchJob(id, { phase: "error", ...classifyError(isNetwork, statusCode) });
          return;
        }
        timers.current[id] = setTimeout(check, 4000);
      }
    };

    check();
  }, [patchJob]);

  const startJob = useCallback(
    (jobId: string, filename: string, sourceMarkdown: string) => {
      fails.current[jobId] = 0;
      setJobs(prev => ({
        ...prev,
        [jobId]: {
          id: jobId,
          filename,
          phase: "running",
          stage: null,
          stageLabel: null,
          stagesDone: [],
          totalStages: MAIN_PIPELINE_STAGE_COUNT,
          pct: 0,
          errorCode: null,
          errorTitle: null,
          errorMsg: null,
          result: null,
          sourceMarkdown,
          pendingAction: null,
        },
      }));
      setLatestJobId(jobId);
      poll(jobId);
    },
    [poll]
  );

  const pauseJob = useCallback(async (jobId: string) => {
    patchJob(jobId, { pendingAction: "pause" });
    try {
      const response = await fetch(apiUrl(`/api/v2/jobs/${jobId}/pause`), {
        method: "POST",
        headers: mutationHeaders(),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok || body.status !== "paused") {
        patchJob(jobId, {
          pendingAction: null,
          errorMsg: body.error ?? "暂停任务失败",
        });
        return false;
      }
      if (timers.current[jobId]) clearTimeout(timers.current[jobId]);
      patchJob(jobId, { phase: "paused", pendingAction: null, errorMsg: null });
      return true;
    } catch {
      patchJob(jobId, { pendingAction: null, errorMsg: "无法连接后端，任务未确认暂停" });
      return false;
    }
  }, [patchJob]);

  const resumeJob = useCallback(async (jobId: string) => {
    patchJob(jobId, { pendingAction: "resume" });
    try {
      const response = await fetch(apiUrl(`/api/v2/jobs/${jobId}/resume`), {
        method: "POST",
        headers: mutationHeaders(),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok || body.status !== "running") {
        patchJob(jobId, {
          pendingAction: null,
          errorMsg: body.error ?? "恢复任务失败",
        });
        return false;
      }
      fails.current[jobId] = 0;
      patchJob(jobId, {
        phase: "running",
        pendingAction: null,
        errorCode: null,
        errorTitle: null,
        errorMsg: null,
        result: null,
      });
      poll(jobId);
      return true;
    } catch {
      patchJob(jobId, { pendingAction: null, errorMsg: "无法连接后端，任务恢复失败" });
      return false;
    }
  }, [patchJob, poll]);

  const cancelJob = useCallback(async (jobId: string) => {
    patchJob(jobId, { pendingAction: "cancel" });
    try {
      const response = await fetch(apiUrl(`/api/v2/jobs/${jobId}/cancel`), {
        method: "POST",
        headers: mutationHeaders(),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok || body.status !== "cancelled") {
        patchJob(jobId, {
          pendingAction: null,
          errorMsg: body.error ?? "取消任务失败",
        });
        return false;
      }
      if (timers.current[jobId]) clearTimeout(timers.current[jobId]);
      setJobs(prev => {
        const next = { ...prev };
        delete next[jobId];
        return next;
      });
      setLatestJobId(prev => (prev === jobId ? null : prev));
      return true;
    } catch {
      patchJob(jobId, { pendingAction: null, errorMsg: "无法连接后端，任务未取消" });
      return false;
    }
  }, [patchJob]);

  const restoreJob = useCallback((job: RestoredJob) => {
    fails.current[job.job_id] = 0;
    const doneCount = job.stages_done?.length ?? 0;
    setJobs(prev => ({
      ...prev,
      [job.job_id]: {
        id: job.job_id,
        filename: job.filename,
        phase: "running",
        stage: job.stage,
        stageLabel: job.stage_label,
        stagesDone: job.stages_done ?? [],
        totalStages: job.total_stages || MAIN_PIPELINE_STAGE_COUNT,
        pct: job.total_stages
          ? Math.round(doneCount / job.total_stages * 100)
          : 0,
        errorCode: null,
        errorTitle: null,
        errorMsg: null,
        result: null,
        sourceMarkdown: job.source_markdown ?? "",
        pendingAction: null,
      },
    }));
    setLatestJobId(job.job_id);
    poll(job.job_id);
  }, [poll]);

  const dismissJob = useCallback((jobId: string) => {
    if (timers.current[jobId]) clearTimeout(timers.current[jobId]);
    setJobs(prev => {
      const next = { ...prev };
      delete next[jobId];
      return next;
    });
    setLatestJobId(prev => (prev === jobId ? null : prev));
  }, []);

  return (
    <JobsCtx.Provider value={{
      jobs,
      latestJobId,
      startJob,
      pauseJob,
      resumeJob,
      cancelJob,
      restoreJob,
      dismissJob,
    }}>
      {children}
    </JobsCtx.Provider>
  );
}

// ── Hook ─────────────────────────────────────────────────────────────────────

export function useJobs(): JobsCtxValue {
  const ctx = useContext(JobsCtx);
  if (!ctx) throw new Error("useJobs must be used inside <JobsProvider>");
  return ctx;
}
