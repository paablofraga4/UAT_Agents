import { API_BASE, withToken } from "./api";

export type AgentEvent =
  | { type: "log"; task_id: string; message: string }
  | { type: "screenshot"; task_id: string; path: string | null }
  | { type: "plan"; task_id: string; plan: any }
  | { type: "step_started"; task_id: string; index: number; action: any }
  | { type: "step_finished"; task_id: string; step: any }
  | { type: "completed"; task_id: string; success: boolean; summary: string; report_path: string }
  | { type: "error"; task_id: string; message: string };

export type TaskSocket = { close: () => void };

const wsBase = () => API_BASE.replace(/^http/, "ws");

/**
 * Subscribe to a task's event stream, transparently reconnecting if the
 * socket drops mid-task. The backend replays each task's full event history
 * to late subscribers, so after a reconnect we skip the events we already
 * processed and resume live.
 */
export function openTaskSocket(
  taskId: string,
  onEvent: (e: AgentEvent) => void
): TaskSocket {
  let ws: WebSocket | null = null;
  let seen = 0;
  let finished = false;
  let closedByUser = false;
  let retries = 0;

  const connect = () => {
    ws = new WebSocket(withToken(`${wsBase()}/ws/tasks/${taskId}`));
    let received = 0;
    ws.onmessage = (m) => {
      let e: AgentEvent;
      try {
        e = JSON.parse(m.data);
      } catch {
        return;
      }
      received++;
      retries = 0;
      if (received <= seen) return; // replayed event we already handled
      seen = received;
      if (e.type === "completed") finished = true;
      onEvent(e);
    };
    ws.onclose = () => {
      if (finished || closedByUser) return;
      if (retries >= 6) {
        onEvent({
          type: "error",
          task_id: taskId,
          message: "Lost connection to the task event stream.",
        });
        return;
      }
      const delay = Math.min(500 * 2 ** retries, 8000);
      retries++;
      setTimeout(() => {
        if (!finished && !closedByUser) connect();
      }, delay);
    };
  };
  connect();

  return {
    close: () => {
      closedByUser = true;
      ws?.close();
    },
  };
}

// ---- Live browser view ----

export type ScreenServerEvent =
  | { t: "meta"; w: number; h: number }
  | { t: "frame"; data: string };

export type ScreenInputEvent =
  | { t: "click"; x: number; y: number; button?: string; clicks?: number }
  | { t: "move"; x: number; y: number }
  | { t: "wheel"; x: number; y: number; dy: number }
  | { t: "key"; key: string; ctrl?: boolean; alt?: boolean; shift?: boolean; meta?: boolean }
  | { t: "type"; text: string };

export type ScreenSocket = {
  send: (e: ScreenInputEvent) => void;
  close: () => void;
};

export function openScreenSocket(
  sessionId: string,
  onEvent: (e: ScreenServerEvent) => void,
  onStatus?: (connected: boolean) => void
): ScreenSocket {
  let ws: WebSocket | null = null;
  let closedByUser = false;
  let retries = 0;

  const connect = () => {
    ws = new WebSocket(withToken(`${wsBase()}/ws/sessions/${sessionId}/screen`));
    ws.onopen = () => {
      retries = 0;
      onStatus?.(true);
    };
    ws.onmessage = (m) => {
      try {
        onEvent(JSON.parse(m.data));
      } catch {}
    };
    ws.onclose = () => {
      onStatus?.(false);
      if (closedByUser || retries >= 6) return;
      const delay = Math.min(500 * 2 ** retries, 8000);
      retries++;
      setTimeout(() => {
        if (!closedByUser) connect();
      }, delay);
    };
  };
  connect();

  return {
    send: (e) => {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(e));
    },
    close: () => {
      closedByUser = true;
      ws?.close();
    },
  };
}
