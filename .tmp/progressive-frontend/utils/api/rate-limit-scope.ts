/** Politicas do contrato ScoutApiV2; rotas desconhecidas ficam isoladas. */
export type ApiRateLimitScope = "default" | "auth" | "crawler" | "poll";

export function parseApiRateLimitScope(value: unknown): ApiRateLimitScope | null {
  return value === "default" || value === "auth" || value === "crawler" || value === "poll" ? value : null;
}

export function apiRateLimitScope(method: string, path: string): ApiRateLimitScope | null {
  if (path === "/auth/me") return "default";
  if (/^\/auth\/(google|callback|refresh|logout)$/.test(path)) return "auth";
  if (method === "POST" && (/^\/crawl(?:\/offer)?$/.test(path) || path === "/match" || path === "/offers/refresh" || /^\/products\/[^/]+\/match-runs$/.test(path))) return "crawler";
  if (method === "GET" && (/^\/products\/[^/]+\/match-runs\/active$/.test(path) || /^\/match-runs\/[^/]+(?:\/live)?$/.test(path) || path === "/notifications" || path === "/notifications/unread-count")) return "poll";
  if (/^\/(products|stores|offers|notifications|match-runs)(?:\/|$)/.test(path)) return "default";
  return null;
}
