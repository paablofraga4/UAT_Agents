import { API_BASE } from "./api";

export type AgentEvent =
  | { type: "log"; task_id: string; message: string }
  | { type: "screenshot"; task_id: string; path: string | null }
  | { type: "plan"; task_id: string; plan: any }
  | { type: "step_started"; task_id: string; index: number; action: any }
  | { type: "step_finished"; task_id: string; step: any }
  | { type: "completed"; task_id: string; success: boolean; summary: string; report_path: string }
  | { type: "error"; task_id: string; message: string };

export function openTaskSocket(taskId: string, onEvent: (e: AgentEvent) => void): WebSocket {
  const wsBase = API_BASE.replace(/^http/, "ws");
  const ws = new WebSocket(`${wsBase}/ws/tasks/${taskId}`);
  ws.onmessage = (m) => {
    try {
      onEvent(JSON.parse(m.data));
    } catch {}
  };
  return ws;
}
