export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

// Must match the backend's API_TOKEN when set; empty = open local backend.
export const API_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN ?? "";

export type TaskMode = "task" | "test_form";

function authHeaders(): Record<string, string> {
  return API_TOKEN ? { "X-API-Key": API_TOKEN } : {};
}

// For URLs the browser fetches directly (img src, WebSockets) where headers
// can't be set.
export function withToken(url: string): string {
  if (!API_TOKEN) return url;
  return `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(API_TOKEN)}`;
}

export async function createSession(url: string) {
  const r = await fetch(`${API_BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ url }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function confirmLogin(sessionId: string) {
  const r = await fetch(`${API_BASE}/sessions/${sessionId}/confirm-login`, {
    method: "POST",
    headers: authHeaders(),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function closeSession(sessionId: string) {
  await fetch(`${API_BASE}/sessions/${sessionId}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
}

export async function createTask(
  sessionId: string,
  instruction: string,
  mode: TaskMode = "task"
) {
  const r = await fetch(`${API_BASE}/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ session_id: sessionId, instruction, mode }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function evidenceUrl(path: string | null | undefined) {
  if (!path) return null;
  return withToken(`${API_BASE}/evidence/${path}`);
}
