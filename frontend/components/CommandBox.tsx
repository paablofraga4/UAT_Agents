"use client";

import { useState } from "react";

import type { TaskMode } from "@/lib/api";

type Props = {
  disabled: boolean;
  onSubmit: (instruction: string, mode: TaskMode) => Promise<void>;
};

export function CommandBox({ disabled, onSubmit }: Props) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState<TaskMode>("task");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!text.trim()) return;
    setBusy(true);
    try {
      await onSubmit(text.trim(), mode);
      setText("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel p-3">
      <div className="flex items-start gap-2">
        <textarea
          className="input min-h-[64px] resize-none flex-1"
          placeholder={
            mode === "task"
              ? "e.g. Open the patients section, create a male patient aged 67, and verify the record was saved."
              : "e.g. Test the patient registration form."
          }
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={disabled || busy}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
        />
        <div className="flex flex-col gap-2">
          <select
            className="input text-xs"
            value={mode}
            onChange={(e) => setMode(e.target.value as TaskMode)}
            disabled={disabled || busy}
            title="Task: run the instruction once. Form test: design and execute a test matrix against the form."
          >
            <option value="task">Task</option>
            <option value="test_form">Form test</option>
          </select>
          <button
            className="btn btn-primary"
            onClick={submit}
            disabled={disabled || busy || !text.trim()}
          >
            {busy ? "Running…" : "Run"}
          </button>
        </div>
      </div>
      <div className="text-[11px] text-zinc-500 mt-1">
        ⌘/Ctrl + Enter to run · session must be authenticated ·{" "}
        {mode === "test_form"
          ? "Form test mode: the agent generates and runs a test matrix, the report shows a pass/fail table"
          : "Task mode: the agent executes your instruction step by step"}
      </div>
    </div>
  );
}
