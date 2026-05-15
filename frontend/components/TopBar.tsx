"use client";

import { useState } from "react";

type Props = {
  sessionId: string | null;
  status: string;
  onOpen: (url: string) => Promise<void>;
  onConfirmLogin: () => Promise<void>;
  onClose: () => Promise<void>;
};

export function TopBar({ sessionId, status, onOpen, onConfirmLogin, onClose }: Props) {
  const [url, setUrl] = useState("https://uat.example.com");
  const [busy, setBusy] = useState(false);

  const open = async () => {
    setBusy(true);
    try { await onOpen(url); } finally { setBusy(false); }
  };
  const confirm = async () => {
    setBusy(true);
    try { await onConfirmLogin(); } finally { setBusy(false); }
  };

  const statusColor =
    status === "authenticated" ? "text-emerald-400"
    : status === "waiting_login" ? "text-amber-400"
    : status === "running" ? "text-sky-400"
    : status === "error" ? "text-rose-400"
    : "text-zinc-400";

  return (
    <div className="panel px-4 py-3 flex items-center gap-3">
      <div className="font-semibold text-accent">UAT Agents</div>
      <input
        className="input flex-1"
        placeholder="https://your-uat-app.example.com"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        disabled={!!sessionId}
      />
      {!sessionId ? (
        <button className="btn btn-primary" onClick={open} disabled={busy}>
          Open Session
        </button>
      ) : (
        <>
          <span className={`tag ${statusColor}`}>{status}</span>
          {status === "waiting_login" && (
            <button className="btn btn-primary" onClick={confirm} disabled={busy}>
              I am logged in
            </button>
          )}
          <button className="btn" onClick={onClose} disabled={busy}>
            Close
          </button>
        </>
      )}
    </div>
  );
}
