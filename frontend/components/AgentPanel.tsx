"use client";

type Step = {
  index: number;
  action: { action: string; description?: string; [k: string]: any };
  status: "pending" | "running" | "completed" | "failed";
  message?: string | null;
  error?: string | null;
};

type Props = {
  interpretedTask: string | null;
  steps: Step[];
  currentIndex: number | null;
};

const statusBadge: Record<string, string> = {
  pending: "text-zinc-500",
  running: "text-sky-400 animate-pulse",
  completed: "text-emerald-400",
  failed: "text-rose-400",
};

export function AgentPanel({ interpretedTask, steps, currentIndex }: Props) {
  return (
    <div className="panel flex-1 flex flex-col min-h-0">
      <div className="px-4 py-2 border-b border-border text-xs text-zinc-400 flex items-center gap-2">
        <span className="tag">agent</span>
        <span>plan & execution</span>
      </div>
      <div className="px-4 py-3 border-b border-border">
        <div className="text-[11px] uppercase text-zinc-500">Interpreted task</div>
        <div className="text-sm">{interpretedTask ?? <span className="text-zinc-600">— waiting —</span>}</div>
      </div>
      <div className="flex-1 scroll p-3 space-y-2">
        {steps.length === 0 && (
          <div className="text-zinc-600 text-sm px-1">No plan yet.</div>
        )}
        {steps.map((s) => (
          <div
            key={s.index}
            className={`border border-border rounded-md p-2 ${
              currentIndex === s.index ? "ring-1 ring-accent/50" : ""
            }`}
          >
            <div className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-2">
                <span className="text-zinc-500">#{s.index + 1}</span>
                <span className="tag">{s.action.action}</span>
              </div>
              <span className={`text-[11px] ${statusBadge[s.status] ?? ""}`}>
                {s.status}
              </span>
            </div>
            <div className="mt-1 text-sm text-zinc-300">
              {s.action.description || JSON.stringify(s.action)}
            </div>
            {s.message && <div className="mt-1 text-xs text-zinc-500">{s.message}</div>}
            {s.error && <div className="mt-1 text-xs text-rose-400">{s.error}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
