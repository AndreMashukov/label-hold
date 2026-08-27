import { useEffect, useState } from "react";
import { getLot, type LotRow } from "../api";

interface Props {
  lotId: string;
  fetcher: (lotId: string) => Promise<LotRow | null>;
  onClose: () => void;
}

export function LotDetail({ lotId, fetcher, onClose }: Props) {
  const [data, setData] = useState<LotRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetcher(lotId)
      .then((d) => {
        if (alive) setData(d);
      })
      .catch((e) => alive && setError(String(e?.message ?? e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [lotId, fetcher]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-header">
          <div className="modal-title">
            <span className="muted">Lot</span>{" "}
            <span className="mono">{lotId}</span>
          </div>
          <button
            type="button"
            className="secondary-btn"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </header>
        <div className="modal-body">
          {loading && <p className="muted">Loading…</p>}
          {error && <p className="banner banner-error">{error}</p>}
          {data && (
            <dl className="detail">
              <dt>Verdict</dt>
              <dd>
                <span className={`status-pill status-${data.status}`}>
                  {data.status}
                </span>
              </dd>
              <dt>Undeclared</dt>
              <dd>
                {data.undeclared && data.undeclared.length > 0
                  ? data.undeclared.join(", ")
                  : "—"}
              </dd>
              <dt>Reason</dt>
              <dd>{data.reason ?? "—"}</dd>
              <dt>Summary</dt>
              <dd>{data.summary ?? "—"}</dd>
              <dt>Write ID</dt>
              <dd className="mono small">{data.write_id ?? "—"}</dd>
              <dt>Source</dt>
              <dd>{data.source ?? "—"}</dd>
              <dt>Updated</dt>
              <dd>{data.updated_at ?? data.ts ?? "—"}</dd>
            </dl>
          )}
        </div>
      </div>
    </div>
  );
}

// Suppress unused import warning for getLot (kept for type-erasure).
void getLot;
