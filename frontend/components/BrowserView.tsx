"use client";

import { useEffect, useRef, useState } from "react";

import { evidenceUrl } from "@/lib/api";
import { openScreenSocket, type ScreenSocket } from "@/lib/ws";

type Props = {
  sessionId: string | null;
  screenshotPath: string | null;
};

/**
 * Live, interactive view of the session's browser. Frames stream from the
 * backend; clicks, scrolling, typing and pasting are forwarded to the real
 * page — this is where the user logs into the target app, entirely from the
 * web UI. Falls back to the latest evidence screenshot when there is no
 * active session.
 */
export function BrowserView({ sessionId, screenshotPath }: Props) {
  const [frame, setFrame] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const viewport = useRef({ w: 1366, h: 820 });
  const sockRef = useRef<ScreenSocket | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  useEffect(() => {
    setFrame(null);
    setLive(false);
    if (!sessionId) return;
    const sock = openScreenSocket(
      sessionId,
      (e) => {
        if (e.t === "meta") viewport.current = { w: e.w, h: e.h };
        else if (e.t === "frame") setFrame(e.data);
      },
      (connected) => setLive(connected)
    );
    sockRef.current = sock;
    return () => {
      sockRef.current = null;
      sock.close();
    };
  }, [sessionId]);

  const toPage = (clientX: number, clientY: number) => {
    const img = imgRef.current;
    if (!img) return null;
    const rect = img.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return null;
    return {
      x: ((clientX - rect.left) / rect.width) * viewport.current.w,
      y: ((clientY - rect.top) / rect.height) * viewport.current.h,
    };
  };

  const showLive = !!sessionId && frame !== null;
  const fallback = evidenceUrl(screenshotPath);

  return (
    <div className="panel flex-1 overflow-hidden flex flex-col">
      <div className="px-4 py-2 border-b border-border text-xs text-zinc-400 flex items-center gap-2">
        <span className="tag">browser view</span>
        {showLive ? (
          <>
            <span className={live ? "text-emerald-400" : "text-amber-400"}>
              {live ? "● live" : "○ reconnecting…"}
            </span>
            <span>click inside to interact — log in to the target app here</span>
          </>
        ) : (
          <span>latest screenshot (refreshes after each step)</span>
        )}
      </div>
      <div
        className="flex-1 bg-black/40 flex items-center justify-center scroll outline-none"
        tabIndex={0}
        onClick={(e) => {
          if (!showLive) return;
          const p = toPage(e.clientX, e.clientY);
          if (p) sockRef.current?.send({ t: "click", x: p.x, y: p.y, clicks: e.detail >= 2 ? 2 : 1 });
        }}
        onWheel={(e) => {
          if (!showLive) return;
          const p = toPage(e.clientX, e.clientY);
          if (p) sockRef.current?.send({ t: "wheel", x: p.x, y: p.y, dy: e.deltaY });
        }}
        onKeyDown={(e) => {
          if (!showLive) return;
          // Let the browser keep its own copy/paste; forward everything else.
          if ((e.ctrlKey || e.metaKey) && ["c", "v"].includes(e.key.toLowerCase())) return;
          e.preventDefault();
          sockRef.current?.send({
            t: "key",
            key: e.key,
            ctrl: e.ctrlKey,
            alt: e.altKey,
            shift: e.shiftKey,
            meta: e.metaKey,
          });
        }}
        onPaste={(e) => {
          if (!showLive) return;
          const text = e.clipboardData.getData("text");
          if (text) sockRef.current?.send({ t: "type", text });
          e.preventDefault();
        }}
      >
        {showLive ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            ref={imgRef}
            src={`data:image/jpeg;base64,${frame}`}
            alt="live browser"
            className="max-w-full max-h-full select-none"
            draggable={false}
          />
        ) : fallback ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={fallback} alt="browser" className="max-w-full max-h-full object-contain" />
        ) : (
          <div className="text-zinc-500 text-sm">
            No session yet. Open one above — the target app will appear here.
          </div>
        )}
      </div>
    </div>
  );
}
