import type { LotRow } from "../api";

interface Props {
  lots: LotRow[];
  onSelect: (lotId: string) => void;
  selectedLotId: string | null;
}

export function VerdictsTable({ lots, onSelect, selectedLotId }: Props) {
  if (lots.length === 0) {
    return (
      <div className="empty">
        <p className="muted">
          No verdicts yet. Run a lot above to see results here.
        </p>
      </div>
    );
  }
  return (
    <div className="table-wrap">
      <table className="verdicts">
        <thead>
          <tr>
            <th>Lot</th>
            <th>Verdict</th>
            <th>Undeclared</th>
            <th>Summary</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          {lots.map((lot) => (
            <tr
              key={lot.lot_id}
              className={selectedLotId === lot.lot_id ? "is-selected" : ""}
              onClick={() => onSelect(lot.lot_id)}
            >
              <td className="mono">{lot.lot_id}</td>
              <td>
                <span className={`status-pill status-${lot.status}`}>
                  {lot.status}
                </span>
              </td>
              <td>
                {lot.undeclared && lot.undeclared.length > 0 ? (
                  <span className="undeclared">{lot.undeclared.join(", ")}</span>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
              <td className="summary-cell">
                {lot.summary ? lot.summary : <span className="muted">—</span>}
              </td>
              <td className="muted">
                {formatTs(lot.updated_at || lot.ts)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatTs(ts?: string): string {
  if (!ts) return "—";
  // Try to parse ISO; fall back to raw.
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}
