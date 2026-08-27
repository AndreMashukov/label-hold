// API client for the label-hold backend (the dashboard BFF in production,
// a local proxy in development). VITE_API_BASE is baked into the build by
// vite.config.ts and resolves to an absolute URL when set. Empty means
// same-origin (works behind a co-located proxy).

declare const __VITE_API_BASE__: string;

function apiBase(): string {
  // Vite's `define` replaces the literal at build time. If the build was
  // produced without VITE_API_BASE, this falls through to "" (same-origin).
  const fromDefine = (() => {
    try {
      return (typeof __VITE_API_BASE__ !== "undefined" ? __VITE_API_BASE__ : "") as string;
    } catch {
      return "";
    }
  })();
  const base = (fromDefine || (import.meta as any).env?.VITE_API_BASE || "").replace(
    /\/+$/,
    ""
  );
  return base;
}

function url(path: string): string {
  const base = apiBase();
  if (!base) return path;
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

export type LotStatus = "held" | "released" | string;

export interface LotRow {
  lot_id: string;
  status: LotStatus;
  undeclared?: string[];
  reason?: string;
  summary?: string;
  write_id?: string;
  ts?: string;
  updated_at?: string;
  source?: string;
  [k: string]: unknown;
}

export interface RunResult {
  status: LotStatus;
  undeclared?: string[];
  reason?: string;
  summary?: string;
  write_id?: string;
  lot_id?: string;
  [k: string]: unknown;
}

async function jsonOrThrow(r: Response): Promise<any> {
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`HTTP ${r.status}: ${text.slice(0, 500)}`);
  }
  return r.json();
}

export async function listLots(): Promise<LotRow[]> {
  const r = await fetch(url("/api/lots"), { credentials: "omit" });
  const data = await jsonOrThrow(r);
  return (data.lots as LotRow[]) ?? [];
}

export async function getLot(lotId: string): Promise<LotRow | null> {
  const r = await fetch(url(`/api/lots/${encodeURIComponent(lotId)}`), {
    credentials: "omit",
  });
  if (r.status === 404) return null;
  const data = await jsonOrThrow(r);
  if (data && data.found === false) return null;
  return data as LotRow;
}

export async function runLot(args: {
  lotId: string;
  spec: File;
  coa: File;
  label: File;
}): Promise<RunResult> {
  const fd = new FormData();
  fd.append("lot_id", args.lotId);
  fd.append("spec", args.spec, args.spec.name);
  fd.append("coa", args.coa, args.coa.name);
  fd.append("label", args.label, args.label.name);
  const r = await fetch(url("/api/run"), { method: "POST", body: fd, credentials: "omit" });
  return (await jsonOrThrow(r)) as RunResult;
}
