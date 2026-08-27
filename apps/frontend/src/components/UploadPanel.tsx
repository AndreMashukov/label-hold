import { useRef, useState } from "react";

interface Props {
  onRun: (args: {
    lotId: string;
    spec: File;
    coa: File;
    label: File;
  }) => Promise<void> | void;
  loading: boolean;
}

const PRESETS = [
  {
    id: "hk-raw-tuna",
    label: "Held (fish missing on label)",
    files: ["spec.png", "coa.png", "label.jpg"],
    autoLotId: "HK-RAW-TUNA",
  },
  {
    id: "hk-multi-allergen",
    label: "Held (wheat + milk + eggs)",
    files: ["spec.png", "coa.png", "label.jpg"],
    autoLotId: "HK-MULTI-ALLERGEN",
  },
  {
    id: "hk-empty-label",
    label: "Held (no allergen panel)",
    files: ["spec.png", "coa.png", "label.jpg"],
    autoLotId: "HK-EMPTY-LABEL",
  },
  {
    id: "hk-tree-nuts-mix",
    label: "Released (all tree nuts declared)",
    files: ["spec.png", "coa.png", "label.jpg"],
    autoLotId: "HK-TREE-NUTS-MIX",
  },
  {
    id: "hk-multi-allergen-released",
    label: "Released (full multi-allergen)",
    files: ["spec.png", "coa.png", "label.jpg"],
    autoLotId: "HK-MULTI-REL",
  },
];

export function UploadPanel({ onRun, loading }: Props) {
  const [lotId, setLotId] = useState("");
  const [spec, setSpec] = useState<File | null>(null);
  const [coa, setCoa] = useState<File | null>(null);
  const [label, setLabel] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState<"spec" | "coa" | "label" | null>(null);
  const fileRefs = {
    spec: useRef<HTMLInputElement>(null),
    coa: useRef<HTMLInputElement>(null),
    label: useRef<HTMLInputElement>(null),
  };

  const ready = !!lotId.trim() && !!spec && !!coa && !!label && !loading;

  const onDrop = (
    e: React.DragEvent<HTMLDivElement>,
    setter: (f: File) => void
  ) => {
    e.preventDefault();
    setDragOver(null);
    const f = e.dataTransfer.files?.[0];
    if (f) setter(f);
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
            disabled={loading}
          />
        </label>
        <div className="field">
          <span className="field-label">Quick presets</span>
          <div className="preset-row">
            {PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                className="preset-btn"
                disabled={loading}
                onClick={async () => {
                  const base = `/fixtures/${p.id}`;
                  const [specF, coaF, labelF] = await Promise.all(
                    p.files.map(async (name) => {
                      const r = await fetch(`${base}/${name}`);
                      if (!r.ok) throw new Error(`Failed to fetch ${base}/${name}`);
                      const blob = await r.blob();
                      return new File([blob], name, { type: blob.type });
                    })
                  );
                  setSpec(specF);
                  setCoa(coaF);
                  setLabel(labelF);
                  const shortId = Math.random().toString(36).slice(2, 6);
                  if (!lotId.trim()) setLotId(`${p.autoLotId}-${shortId}`);
                }}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="dropzone-row">
        <Dropzone
          name="spec"
          label="Spec"
          accept="image/png,image/jpeg,application/pdf"
          file={spec}
          onFile={setSpec}
          onDragOver={setDragOver}
          onDrop={onDrop}
          dragOver={dragOver === "spec"}
          fileRef={fileRefs.spec}
          disabled={loading}
        />
        <Dropzone
          name="coa"
          label="CoA"
          accept="image/png,image/jpeg,application/pdf"
          file={coa}
          onFile={setCoa}
          onDragOver={setDragOver}
          onDrop={onDrop}
          dragOver={dragOver === "coa"}
          fileRef={fileRefs.coa}
          disabled={loading}
        />
        <Dropzone
          name="label"
          label="Label"
          accept="image/png,image/jpeg,application/pdf"
          file={label}
          onFile={setLabel}
          onDragOver={setDragOver}
          onDrop={onDrop}
          dragOver={dragOver === "label"}
          fileRef={fileRefs.label}
          disabled={loading}
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
        {!ready && !loading && (
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
      <div className="dropzone-hint">
        {file ? file.name : "Drop a file or click to browse"}
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
