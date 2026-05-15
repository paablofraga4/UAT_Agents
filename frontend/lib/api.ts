export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export async function createSession(url: string) {
  const r = await fetch(`${API_BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function confirmLogin(sessionId: string) {
  const r = await fetch(`${API_BASE}/sessions/${sessionId}/confirm-login`, {
    method: "POST",
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function closeSession(sessionId: string) {
  await fetch(`${API_BASE}/sessions/${sessionId}`, { method: "DELETE" });
}

export async function createTask(sessionId: string, instruction: string) {
  const r = await fetch(`${API_BASE}/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, instruction }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function evidenceUrl(path: string | null | undefined) {
  if (!path) return null;
  return `${API_BASE}/evidence/${path}`;
}
