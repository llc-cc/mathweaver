const DESKTOP_USER_AGENT_MARKER = "MathWeaverDesktop/";

export function isDesktopRuntime(): boolean {
  return typeof navigator !== "undefined"
    && navigator.userAgent.includes(DESKTOP_USER_AGENT_MARKER);
}

