"use client";

export type LogEntry = { ts: string; text: string; kind?: "info" | "error" };

export function LogsPanel({ logs }: { logs: LogEntry[] }) {
  return (
    <div className="panel flex-1 flex flex-col min-h-0">
      <div className="px-4 py-2 border-b border-border text-xs text-zinc-400 flex items-center gap-2">
        <span className="tag">logs</span>
      </div>
      <div className="flex-1 scroll p-3 font-mono text-xs space-y-1">
        {logs.length === 0 && <div className="text-zinc-600">No events yet.</div>}
        {logs.map((l, i) => (
          <div key={i} className={l.kind === "error" ? "text-rose-400" : "text-zinc-300"}>
            <span className="text-zinc-600">{l.ts}</span> {l.text}
          </div>
        ))}
      </div>
    </div>
  );
}
