interface Props {
  theme: "light" | "dark" | "system";
  onChange: (t: "light" | "dark" | "system") => void;
}

export function ThemeToggle({ theme, onChange }: Props) {
  return (
    <div className="theme-toggle" role="group" aria-label="Theme">
      {(["light", "system", "dark"] as const).map((t) => (
        <button
          key={t}
          type="button"
          className={`theme-btn${theme === t ? " is-active" : ""}`}
          onClick={() => onChange(t)}
        >
          {t}
        </button>
      ))}
    </div>
  );
}
