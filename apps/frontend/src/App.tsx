import { useCallback, useEffect, useRef, useState } from "react";
import { listLots, runLot, getLot, type LotRow, type RunResult } from "./api";
import { UploadPanel } from "./components/UploadPanel";
import { VerdictsTable } from "./components/VerdictsTable";
import { ThemeToggle } from "./components/ThemeToggle";
import { LotDetail } from "./components/LotDetail";

type Theme = "light" | "dark" | "system";

const POLL_INTERVAL_MS = 5000;

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
  const pollHandle = useRef<number | null>(null);

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

  // Poll /api/lots every 5s. Stops when the tab is hidden.
  const refresh = useCallback(async () => {
    try {
      const rows = await listLots();
      setLots(rows);
      setError(null);
    } catch (e: any) {
      setError(e?.message ?? String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
    const start = () => {
      if (pollHandle.current != null) return;
      pollHandle.current = window.setInterval(refresh, POLL_INTERVAL_MS);
    };
    const stop = () => {
      if (pollHandle.current != null) {
        window.clearInterval(pollHandle.current);
        pollHandle.current = null;
      }
    };
    const onVis = () => (document.hidden ? stop() : (refresh(), start()));
    start();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [refresh]);

  const handleRun = useCallback(
    async (args: { lotId: string; spec: File; coa: File; label: File }) => {
      setLoading(true);
      setError(null);
      try {
        const result = await runLot(args);
        setLastRun(result);
        // Optimistic refresh after a beat — the consumer materialization takes ~5s.
        setTimeout(refresh, 1500);
      } catch (e: any) {
        setError(e?.message ?? String(e));
      } finally {
        setLoading(false);
      }
    },
    [refresh]
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
          <a
            className="link"
            href="https://frontend-472857763269.asia-southeast1.run.app/"
            target="_blank"
            rel="noreferrer"
          >
            self ↗
          </a>
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
          <UploadPanel onRun={handleRun} loading={loading} />
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
              {lots.length} lot{lots.length === 1 ? "" : "s"} · polling every{" "}
              {POLL_INTERVAL_MS / 1000}s
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
          LlmAgent &gt; LoopAgent &gt; poster
        </span>
      </footer>
    </div>
  );
}
