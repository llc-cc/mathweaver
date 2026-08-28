const DEFAULT_API_ORIGIN = "http://127.0.0.1:5001";
const SAME_ORIGIN = "__SAME_ORIGIN__";

export function apiUrl(path: string) {
  const configured = import.meta.env.VITE_API_ORIGIN;
  const base = configured === SAME_ORIGIN && typeof window !== "undefined"
    ? window.location.origin
    : configured || DEFAULT_API_ORIGIN;
  return `${base}${path}`;
}
