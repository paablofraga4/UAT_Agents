"use client";

import { useState } from "react";

type Props = {
  disabled: boolean;
  onSubmit: (instruction: string) => Promise<void>;
};

export function CommandBox({ disabled, onSubmit }: Props) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!text.trim()) return;
    setBusy(true);
    try {
      await onSubmit(text.trim());
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
          placeholder="e.g. Open the patients section, create a male patient aged 67, and verify the record was saved."
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={disabled || busy}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
        />
        <button
          className="btn btn-primary"
          onClick={submit}
          disabled={disabled || busy || !text.trim()}
        >
          {busy ? "Running…" : "Run"}
        </button>
      </div>
      <div className="text-[11px] text-zinc-500 mt-1">
        ⌘/Ctrl + Enter to run · session must be authenticated
      </div>
    </div>
  );
}
