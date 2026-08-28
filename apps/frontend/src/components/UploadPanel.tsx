import { useEffect, useRef, useState } from "react";

interface Props {
  onRun: (args: {
    lotId: string;
    spec: File;
    coa: File;
    label: File;
  }) => Promise<void> | void;
  onClear?: () => void;
  loading: boolean;
}

type Preset = {
  id: string;
  label: string;
  /** Directory under /fixtures/ to fetch spec/label (and CoA unless emptyCoa). */
  fixtureDir: string;
  files: { spec: string; coa: string; label: string };
  autoLotId: string;
  /** Synthesize a 0-byte text CoA so the graph takes the incomplete_packet path. */
  emptyCoa?: boolean;
};

const PRESETS: Preset[] = [
  {
    id: "hk-raw-tuna",
    label: "Held (fish missing on label)",
    fixtureDir: "hk-raw-tuna",
    files: { spec: "spec.png", coa: "coa.png", label: "label.jpg" },
    autoLotId: "HK-RAW-TUNA",
  },
  {
    id: "hk-multi-allergen",
    label: "Held (wheat + milk + eggs)",
    fixtureDir: "hk-multi-allergen",
    files: { spec: "spec.png", coa: "coa.png", label: "label.jpg" },
    autoLotId: "HK-MULTI-ALLERGEN",
  },
  {
    id: "hk-empty-label",
    label: "Held (no allergen panel)",
    fixtureDir: "hk-empty-label",
    files: { spec: "spec.png", coa: "coa.png", label: "label.jpg" },
    autoLotId: "HK-EMPTY-LABEL",
  },
  {
    id: "hk-incomplete",
    label: "Held (incomplete packet)",
    fixtureDir: "hk-multi-allergen",
    files: { spec: "spec.png", coa: "coa.png", label: "label.jpg" },
    autoLotId: "HK-INCOMPLETE",
    emptyCoa: true,
  },
  {
    id: "hk-tree-nuts-mix",
    label: "Released (all tree nuts declared)",
    fixtureDir: "hk-tree-nuts-mix",
    files: { spec: "spec.png", coa: "coa.png", label: "label.jpg" },
    autoLotId: "HK-TREE-NUTS-MIX",
  },
  {
    id: "hk-multi-allergen-released",
    label: "Released (full multi-allergen)",
    fixtureDir: "hk-multi-allergen-released",
    files: { spec: "spec.png", coa: "coa.png", label: "label.jpg" },
    autoLotId: "HK-MULTI-REL",
  },
];

async function fileFromFixture(dir: string, name: string): Promise<File> {
  const path = `/fixtures/${dir}/${name}`;
  const r = await fetch(path);
  if (!r.ok) throw new Error(`Failed to fetch ${path}`);
  const blob = await r.blob();
  const type =
    blob.type ||
    (name.endsWith(".png")
      ? "image/png"
      : name.endsWith(".jpg") || name.endsWith(".jpeg")
        ? "image/jpeg"
        : "text/plain");
  return new File([blob], name, { type });
}

export function UploadPanel({ onRun, onClear, loading }: Props) {
  const [lotId, setLotId] = useState("");
  const [spec, setSpec] = useState<File | null>(null);
  const [coa, setCoa] = useState<File | null>(null);
  const [label, setLabel] = useState<File | null>(null);
  const [activePreset, setActivePreset] = useState<string | null>(null);
  const [presetBusy, setPresetBusy] = useState(false);
  const [presetError, setPresetError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<"spec" | "coa" | "label" | null>(null);
  const fileRefs = {
    spec: useRef<HTMLInputElement>(null),
    coa: useRef<HTMLInputElement>(null),
    label: useRef<HTMLInputElement>(null),
  };

  const busy = loading || presetBusy;
  const ready = !!lotId.trim() && !!spec && !!coa && !!label && !busy;
  const canClear = !busy && (!!lotId || !!spec || !!coa || !!label || !!activePreset);

  const resetFileInputs = () => {
    for (const key of ["spec", "coa", "label"] as const) {
      const el = fileRefs[key].current;
      if (el) el.value = "";
    }
  };

  const clearAll = () => {
    setLotId("");
    setSpec(null);
    setCoa(null);
    setLabel(null);
    setActivePreset(null);
    setPresetError(null);
    resetFileInputs();
    onClear?.();
  };

  const applyPreset = async (p: Preset) => {
    setPresetError(null);
    setActivePreset(p.id);
    setLotId(`${p.autoLotId}-${Math.random().toString(36).slice(2, 6)}`);
    setPresetBusy(true);
    try {
      const specF = await fileFromFixture(p.fixtureDir, p.files.spec);
      const labelF = await fileFromFixture(p.fixtureDir, p.files.label);
      const coaF = p.emptyCoa
        ? new File([], "coa-missing.txt", { type: "text/plain" })
        : await fileFromFixture(p.fixtureDir, p.files.coa);
      setSpec(specF);
      setCoa(coaF);
      setLabel(labelF);
      resetFileInputs();
    } catch (e) {
      setSpec(null);
      setCoa(null);
      setLabel(null);
      setPresetError(e instanceof Error ? e.message : String(e));
    } finally {
      setPresetBusy(false);
    }
  };

  const onDrop = (
    e: React.DragEvent<HTMLDivElement>,
    setter: (f: File) => void
  ) => {
    e.preventDefault();
    setDragOver(null);
    const f = e.dataTransfer.files?.[0];
    if (f) {
      setActivePreset(null);
      setter(f);
    }
  };

  const submit = async () => {
    if (!ready || !spec || !coa || !label) return;
    await onRun({ lotId: lotId.trim(), spec, coa, label });
  };

  return (
    <div className="upload-panel">
      <div className="upload-row">
        <label className="field">
          <span className="field-label">Lot ID</span>
          <input
            className="text-input"
            value={lotId}
            placeholder="e.g. HK-FRONTEND-001"
            onChange={(e) => setLotId(e.target.value)}
            disabled={busy}
          />
        </label>
        <div className="field">
          <span className="field-label">Quick presets</span>
          <div className="preset-row">
            {PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                className={`preset-btn${activePreset === p.id ? " is-active" : ""}`}
                disabled={busy}
                aria-pressed={activePreset === p.id}
                onClick={() => applyPreset(p)}
              >
                {p.label}
              </button>
            ))}
          </div>
          {presetBusy && (
            <span className="muted">Loading preset files…</span>
          )}
          {presetError && <span className="form-error">{presetError}</span>}
        </div>
      </div>

      <div className="dropzone-row">
        <Dropzone
          name="spec"
          label="Spec"
          accept="image/png,image/jpeg,application/pdf"
          file={spec}
          onFile={(f) => {
            setActivePreset(null);
            setSpec(f);
          }}
          onDragOver={setDragOver}
          onDrop={onDrop}
          dragOver={dragOver === "spec"}
          fileRef={fileRefs.spec}
          disabled={busy}
        />
        <Dropzone
          name="coa"
          label="CoA"
          accept="image/png,image/jpeg,application/pdf"
          file={coa}
          onFile={(f) => {
            setActivePreset(null);
            setCoa(f);
          }}
          onDragOver={setDragOver}
          onDrop={onDrop}
          dragOver={dragOver === "coa"}
          fileRef={fileRefs.coa}
          disabled={busy}
        />
        <Dropzone
          name="label"
          label="Label"
          accept="image/png,image/jpeg,application/pdf"
          file={label}
          onFile={(f) => {
            setActivePreset(null);
            setLabel(f);
          }}
          onDragOver={setDragOver}
          onDrop={onDrop}
          dragOver={dragOver === "label"}
          fileRef={fileRefs.label}
          disabled={busy}
        />
      </div>

      <div className="upload-actions">
        <button
          type="button"
          className="primary-btn"
          onClick={submit}
          disabled={!ready}
        >
          {loading ? "Running…" : "Run label hold"}
        </button>
        <button
          type="button"
          className="secondary-btn"
          onClick={clearAll}
          disabled={!canClear}
        >
          Clear all
        </button>
        {!ready && !busy && (
          <span className="muted">
            Provide a lot ID and all three files (or pick a preset).
          </span>
        )}
      </div>
    </div>
  );
}

interface DropzoneProps {
  name: string;
  label: string;
  accept: string;
  file: File | null;
  onFile: (f: File) => void;
  onDragOver: (slot: "spec" | "coa" | "label" | null) => void;
  onDrop: (
    e: React.DragEvent<HTMLDivElement>,
    setter: (f: File) => void
  ) => void;
  dragOver: boolean;
  fileRef: React.RefObject<HTMLInputElement>;
  disabled: boolean;
}

function Dropzone({
  name,
  label,
  accept,
  file,
  onFile,
  onDragOver,
  onDrop,
  dragOver,
  fileRef,
  disabled,
}: DropzoneProps) {
  const [preview, setPreview] = useState<string | null>(null);
  useEffect(() => {
    if (!file || file.size === 0 || !file.type.startsWith("image/")) {
      setPreview(null);
      return;
    }
    const href = URL.createObjectURL(file);
    setPreview(href);
    return () => URL.revokeObjectURL(href);
  }, [file]);

  return (
    <div
      className={`dropzone${dragOver ? " is-over" : ""}${file ? " has-file" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        onDragOver(name as any);
      }}
      onDragLeave={() => onDragOver(null)}
      onDrop={(e) => onDrop(e, onFile)}
      onClick={() => fileRef.current?.click()}
      role="button"
      tabIndex={0}
      aria-disabled={disabled}
    >
      <div className="dropzone-label">{label}</div>
      {preview && (
        <img className="dropzone-thumb" src={preview} alt="" />
      )}
      <div className="dropzone-hint">
        {file
          ? file.size === 0
            ? `${file.name} (empty)`
            : file.name
          : "Drop a file or click to browse"}
      </div>
      <input
        ref={fileRef}
        type="file"
        accept={accept}
        style={{ display: "none" }}
        disabled={disabled}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
        }}
      />
    </div>
  );
}
