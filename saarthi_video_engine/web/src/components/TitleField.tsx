export const TITLE_LIMIT = 120;

export function TitleField({
  value,
  onChange,
  disabled = false,
  id = "video-title",
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  id?: string;
}) {
  return (
    <label className="block text-sm" htmlFor={id}>
      <span className="font-medium text-ink">Video title (optional)</span>
      <input
        id={id}
        className="mt-1 w-full rounded-xl border border-line px-3 py-2 text-ink"
        maxLength={TITLE_LIMIT}
        placeholder="e.g. From [[NEET]] to [[UPSC]]"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      />
      <span className="mt-1 block text-slate-500">
        Hindi, English, or Hinglish. It is used exactly as typed and never translated. Wrap a word in [[ ]] to show it in yellow. You can also add or change it after processing.
      </span>
    </label>
  );
}
