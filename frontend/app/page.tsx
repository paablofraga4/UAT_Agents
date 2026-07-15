"use client";

import { useCallback, useRef, useState } from "react";

import { AgentPanel } from "@/components/AgentPanel";
import { BrowserView } from "@/components/BrowserView";
import { CommandBox } from "@/components/CommandBox";
import { LogsPanel, type LogEntry } from "@/components/LogsPanel";
import { ReportSection } from "@/components/ReportSection";
import { TopBar } from "@/components/TopBar";
import {
  closeSession,
  confirmLogin,
  createSession,
  createTask,
  type TaskMode,
} from "@/lib/api";
import { openTaskSocket, type AgentEvent, type TaskSocket } from "@/lib/ws";

type Step = {
  index: number;
  action: any;
  status: "pending" | "running" | "completed" | "failed";
  message?: string | null;
  error?: string | null;
};

const now = () => new Date().toLocaleTimeString();

export default function Page() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionStatus, setSessionStatus] = useState<string>("idle");
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [screenshotPath, setScreenshotPath] = useState<string | null>(null);
  const [interpretedTask, setInterpretedTask] = useState<string | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [currentIndex, setCurrentIndex] = useState<number | null>(null);
  const [runStatus, setRunStatus] = useState<"idle" | "running" | "success" | "failed">("idle");
  const [summary, setSummary] = useState<string | null>(null);
  const [reportPath, setReportPath] = useState<string | null>(null);
  const wsRef = useRef<TaskSocket | null>(null);

  const log = (text: string, kind: LogEntry["kind"] = "info") =>
    setLogs((prev) => [...prev, { ts: now(), text, kind }]);

  const handleOpen = useCallback(async (url: string) => {
    try {
      log(`Creating browser session for ${url}…`);
      const s = await createSession(url);
      setSessionId(s.session_id);
      setSessionStatus(s.status);
      log(`Session ${s.session_id} created — log in to the target app in the live view below.`);
    } catch (e: any) {
      log(`Failed to create session: ${e.message}`, "error");
    }
  }, []);

  const handleConfirmLogin = useCallback(async () => {
    if (!sessionId) return;
    try {
      const s = await confirmLogin(sessionId);
      setSessionStatus(s.status);
      log("Login confirmed — session is authenticated.");
    } catch (e: any) {
      log(`Confirm login failed: ${e.message}`, "error");
    }
  }, [sessionId]);

  const handleClose = useCallback(async () => {
    if (!sessionId) return;
    wsRef.current?.close();
    wsRef.current = null;
    await closeSession(sessionId);
    setSessionId(null);
    setSessionStatus("idle");
    setScreenshotPath(null);
    setSteps([]);
    setInterpretedTask(null);
    setRunStatus("idle");
    setSummary(null);
    setReportPath(null);
    log("Session closed.");
  }, [sessionId]);

  const handleRun = useCallback(async (instruction: string, mode: TaskMode) => {
    if (!sessionId) return;
    setSteps([]);
    setInterpretedTask(null);
    setSummary(null);
    setReportPath(null);
    setRunStatus("running");
    log(`Submitting ${mode === "test_form" ? "form test" : "task"}: ${instruction}`);

    let task;
    try {
      task = await createTask(sessionId, instruction, mode);
    } catch (e: any) {
      log(`Task creation failed: ${e.message}`, "error");
      setRunStatus("failed");
      return;
    }

    wsRef.current?.close();
    wsRef.current = openTaskSocket(task.task_id, handleEvent);
  }, [sessionId]);

  const handleEvent = useCallback((e: AgentEvent) => {
    switch (e.type) {
      case "log":
        log(e.message);
        break;
      case "screenshot":
        if (e.path) setScreenshotPath(e.path);
        break;
      case "plan": {
        const plan = e.plan;
        setInterpretedTask(plan.interpreted_task ?? "");
        setSteps(
          (plan.steps ?? []).map((a: any, idx: number) => ({
            index: idx,
            action: a,
            status: "pending",
          }))
        );
        log(`Plan generated (${plan.steps?.length ?? 0} steps).`);
        break;
      }
      case "step_started":
        setCurrentIndex(e.index);
        setSteps((prev) =>
          prev.map((s) => (s.index === e.index ? { ...s, status: "running" } : s))
        );
        log(`▶ step #${e.index + 1}: ${e.action.action}`);
        break;
      case "step_finished": {
        const step = e.step;
        setSteps((prev) =>
          prev.map((s) =>
            s.index === step.index
              ? {
                  ...s,
                  status: step.status,
                  message: step.message,
                  error: step.error,
                }
              : s
          )
        );
        if (step.screenshot) setScreenshotPath(step.screenshot);
        log(
          step.status === "failed"
            ? `✗ step #${step.index + 1}: ${step.error}`
            : `✓ step #${step.index + 1}: ${step.message ?? "done"}`,
          step.status === "failed" ? "error" : "info"
        );
        break;
      }
      case "completed":
        setRunStatus(e.success ? "success" : "failed");
        setSummary(e.summary);
        setReportPath(e.report_path);
        setCurrentIndex(null);
        log(`Task ${e.success ? "succeeded" : "failed"}. Report → ${e.report_path}`);
        break;
      case "error":
        setRunStatus("failed");
        log(`agent error: ${e.message}`, "error");
        break;
    }
  }, []);

  const canRun = sessionStatus === "authenticated" && runStatus !== "running";

  return (
    <main className="min-h-screen flex flex-col gap-3 p-3">
      <TopBar
        sessionId={sessionId}
        status={sessionStatus}
        onOpen={handleOpen}
        onConfirmLogin={handleConfirmLogin}
        onClose={handleClose}
      />
      <div className="flex gap-3 flex-1 min-h-0">
        <div className="flex flex-col gap-3 flex-1 min-h-0">
          <BrowserView sessionId={sessionId} screenshotPath={screenshotPath} />
          <CommandBox disabled={!canRun} onSubmit={handleRun} />
          <ReportSection status={runStatus} summary={summary} reportPath={reportPath} />
        </div>
        <div className="flex flex-col gap-3 w-[420px] min-h-0">
          <AgentPanel
            interpretedTask={interpretedTask}
            steps={steps}
            currentIndex={currentIndex}
          />
          <LogsPanel logs={logs} />
        </div>
      </div>
    </main>
  );
}
