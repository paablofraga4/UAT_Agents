"use client";

import { evidenceUrl } from "@/lib/api";

type Props = {
  status: "idle" | "running" | "success" | "failed";
  summary: string | null;
  reportPath: string | null;
};

export function ReportSection({ status, summary, reportPath }: Props) {
  const reportUrl = evidenceUrl(reportPath);
  const color =
    status === "success" ? "text-emerald-400"
    : status === "failed" ? "text-rose-400"
    : status === "running" ? "text-sky-400"
    : "text-zinc-500";
  return (
    <div className="panel p-3">
      <div className="flex items-center gap-3">
        <span className="tag">report</span>
        <span className={`text-sm font-medium ${color}`}>
          {status === "idle" ? "no run yet" : status}
        </span>
        {reportUrl && (
          <a
            className="ml-auto text-xs text-accent underline"
            href={reportUrl}
            target="_blank"
            rel="noreferrer"
          >
            open report.md ↗
          </a>
        )}
      </div>
      {summary && <p className="text-sm text-zinc-300 mt-2">{summary}</p>}
    </div>
  );
}
