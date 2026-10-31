import { useId, useState } from "react";
import { Upload } from "lucide-react";

type VideoFieldProps = {
  label: string;
  hint: string;
  disabled?: boolean;
  onFile: (file: File) => void;
};

export function VideoField({ label, hint, disabled = false, onFile }: VideoFieldProps) {
  const id = useId();
  const [over, setOver] = useState(false);
  return (
    <div>
      <label
        htmlFor={id}
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          if (disabled) return;
          const file = event.dataTransfer.files?.[0];
          if (file) onFile(file);
        }}
        className={`block cursor-pointer rounded-2xl border border-dashed px-5 py-8 text-center transition ${
          over ? "border-teal bg-teal/5" : "border-line bg-white"
        } ${disabled ? "cursor-not-allowed opacity-60" : "hover:border-teal"}`}
      >
        <Upload className="mx-auto text-blue" aria-hidden="true" />
        <span className="mt-3 block font-medium text-ink">{label}</span>
        <span className="mt-1 block text-sm text-slate-600">{hint}</span>
        <input
          id={id}
          type="file"
          accept="video/mp4,video/webm,video/quicktime,.mkv,.m4v"
          className="sr-only"
          disabled={disabled}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) onFile(file);
          }}
        />
      </label>
    </div>
  );
}
