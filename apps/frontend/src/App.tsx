import { useCallback, useEffect, useRef, useState } from "react";
import { listLots, runLot, getLot, url, type LotRow, type RunResult } from "./api";
import { UploadPanel } from "./components/UploadPanel";
import { VerdictsTable } from "./components/VerdictsTable";
import { ThemeToggle } from "./components/ThemeToggle";
import { LotDetail } from "./components/LotDetail";

type Theme = "light" | "dark" | "system";

type StreamChange = {
  lot_id: string;
  type: "ADDED" | "MODIFIED" | "REMOVED";
  row?: LotRow;
};

export default function App() {
  const [lots, setLots] = useState<LotRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<RunResult | null>(null);
  const [selectedLot, setSelectedLot] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>(() => {
    if (typeof window === "undefined") return "system";
    return (localStorage.getItem("lh-theme") as Theme) ?? "system";
  });
  // Connection state for the /api/stream SSE feed.
  const [streamConnected, setStreamConnected] = useState(false);
  const streamRef = useRef<EventSource | null>(null);

  // Apply theme to <html> data-theme attribute.
  useEffect(() => {
    const root = document.documentElement;
    const apply = (t: Theme) => {
      if (t === "system") {
        const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
        root.dataset.theme = prefersDark ? "dark" : "light";
      } else {
        root.dataset.theme = t;
      }
    };
    apply(theme);
    localStorage.setItem("lh-theme", theme);
  }, [theme]);

  // Open an SSE connection to /api/stream. The server holds a Firestore
  // on_snapshot() watch on lots-listen/, so every lean-row write appears
  // in this EventSource in real time. Reconnect on error; pause when the
  // tab is hidden.
  const mergeRow = useCallback((row: LotRow) => {
    setLots((prev) => {
      const without = prev.filter((r) => r.lot_id !== row.lot_id);
      return [row, ...without];
    });
  }, []);
  const removeRow = useCallback((lot_id: string) => {
    setLots((prev) => prev.filter((r) => r.lot_id !== lot_id));
  }, []);

  useEffect(() => {
    let cancelled = false;
    let es: EventSource | null = null;
    let retryTimer: number | null = null;

    const connect = () => {
      if (cancelled) return;
      try {
        es = new EventSource(url("/api/stream"), { withCredentials: false });
      } catch (e) {
        // EventSource constructor doesn't normally throw; treat as fatal.
        setError(`EventSource ctor failed: ${String(e)}`);
        return;
      }
      streamRef.current = es;
      es.addEventListener("open", () => setStreamConnected(true));
      es.addEventListener("error", () => {
        // The browser will auto-reconnect, but it never gets a chance if
        // the stream is in an error state. Close + retry after a beat.
        setStreamConnected(false);
        if (es) {
          es.close();
          es = null;
          streamRef.current = null;
        }
        if (!cancelled) {
          retryTimer = window.setTimeout(connect, 2000);
        }
      });
      es.addEventListener("snapshot", (ev: MessageEvent) => {
        try {
          const msg = JSON.parse(ev.data) as { rows: LotRow[] };
          setLots(Array.isArray(msg.rows) ? msg.rows : []);
        } catch {
          /* ignore parse errors */
        }
      });
      es.addEventListener("change", (ev: MessageEvent) => {
        try {
          const msg = JSON.parse(ev.data) as StreamChange;
          if (msg.type === "REMOVED") {
            removeRow(msg.lot_id);
          } else if (msg.row) {
            mergeRow(msg.row);
          }
        } catch {
          /* ignore parse errors */
        }
      });
    };

    const start = () => {
      if (streamRef.current) return;
      connect();
    };
    const stop = () => {
      cancelled = true;
      if (retryTimer != null) {
        window.clearTimeout(retryTimer);
        retryTimer = null;
      }
      if (streamRef.current) {
        streamRef.current.close();
        streamRef.current = null;
        setStreamConnected(false);
      }
    };

    start();
    const onVis = () => {
      if (document.hidden) {
        stop();
      } else {
        cancelled = false;
        start();
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [mergeRow, removeRow]);

  // Fallback: if the SSE feed hasn't delivered a snapshot within 8s of
  // mount, do a one-shot /api/lots fetch so the UI never appears empty.
  // The streaming feed will then take over (it sends its own initial
  // snapshot event on connect).
  useEffect(() => {
    const timer = window.setTimeout(async () => {
      if (lots.length > 0) return;
      try {
        const rows = await listLots();
        setLots(rows);
      } catch {
        /* SSE will catch it */
      }
    }, 8000);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleRun = useCallback(
    async (args: { lotId: string; spec: File; coa: File; label: File }) => {
      setLoading(true);
      setError(null);
      try {
        const result = await runLot(args);
        setLastRun(result);
        // No need to refresh — the SSE feed will push the new row when
        // leanview-consumer materializes it (typically <2s after /run).
        // We still do a one-shot in case the feed has been paused.
        if (!streamConnected) {
          window.setTimeout(async () => {
            try {
              const rows = await listLots();
              setLots(rows);
            } catch {
              /* SSE will catch it */
            }
          }, 2500);
        }
      } catch (e: any) {
        setError(e?.message ?? String(e));
      } finally {
        setLoading(false);
      }
    },
    [streamConnected]
  );

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="wordmark">harbor kitchen</span>
          <span className="divider">·</span>
          <span className="product">label hold</span>
        </div>
        <div className="topbar-actions">
          <span className={`live-dot ${streamConnected ? "live" : "dead"}`} aria-hidden="true" />
          <span className="muted">{streamConnected ? "live" : "offline"}</span>
          <ThemeToggle theme={theme} onChange={setTheme} />
        </div>
      </header>

      <main className="main">
        <section className="hero">
          <h1>Decide if a lot ships.</h1>
          <p className="lede">
            Upload the spec, the supplier CoA, and a photo of the printed
            label. The composite agent graph ingests all three in parallel,
            computes the deterministic allergen match, and writes a held /
            released verdict that downstream QA sees in real time.
          </p>
        </section>

        <section className="grid">
          <UploadPanel
            onRun={handleRun}
            onClear={() => {
              setLastRun(null);
              setError(null);
            }}
            loading={loading}
          />
          {lastRun && (
            <div className={`status-pill status-${lastRun.status}`}>
              <span className="pill-label">Latest run</span>
              <span className="pill-value">
                {lastRun.status.toUpperCase()}
              </span>
              {lastRun.undeclared && lastRun.undeclared.length > 0 && (
                <span className="pill-detail">
                  undeclared: {lastRun.undeclared.join(", ")}
                </span>
              )}
              {lastRun.summary && (
                <span className="pill-summary">{lastRun.summary}</span>
              )}
            </div>
          )}
        </section>

        {error && (
          <div className="banner banner-error" role="alert">
            {error}
          </div>
        )}

        <section className="panel">
          <header className="panel-header">
            <h2>Recent verdicts</h2>
            <span className="muted">
              {lots.length} lot{lots.length === 1 ? "" : "s"} ·{" "}
              {streamConnected ? "live via Firestore CDC" : "reconnecting…"}
            </span>
          </header>
          <VerdictsTable
            lots={lots}
            onSelect={(lotId) => setSelectedLot(lotId)}
            selectedLotId={selectedLot}
          />
        </section>

        {selectedLot && (
          <LotDetail
            lotId={selectedLot}
            fetcher={getLot}
            onClose={() => setSelectedLot(null)}
          />
        )}
      </main>

      <footer className="footer">
        <span className="muted">
          label-hold · ADK SequentialAgent &gt; ParallelAgent &gt; matcher
          LlmAgent &gt; LoopAgent &gt; poster · CDC from Firestore to bus
        </span>
      </footer>
    </div>
  );
}
