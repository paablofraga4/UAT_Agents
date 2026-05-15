"use client";

import { evidenceUrl } from "@/lib/api";

export function BrowserView({ screenshotPath }: { screenshotPath: string | null }) {
  const src = evidenceUrl(screenshotPath);
  return (
    <div className="panel flex-1 overflow-hidden flex flex-col">
      <div className="px-4 py-2 border-b border-border text-xs text-zinc-400 flex items-center gap-2">
        <span className="tag">browser view</span>
        <span>latest screenshot (refreshes after each step)</span>
      </div>
      <div className="flex-1 bg-black/40 flex items-center justify-center scroll">
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={src} alt="browser" className="max-w-full max-h-full object-contain" />
        ) : (
          <div className="text-zinc-500 text-sm">
            No screenshot yet. Open a session and run a task.
          </div>
        )}
      </div>
    </div>
  );
}
