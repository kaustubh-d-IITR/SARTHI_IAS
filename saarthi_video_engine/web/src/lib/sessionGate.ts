import type { BrollDraft } from "../types";

export function canPrepareAuto(uploadId: string | null, busy: boolean): boolean {
  return Boolean(uploadId) && !busy;
}

export function canPrepareBroll(rawUploadId: string | null, entries: BrollDraft[], busy: boolean): boolean {
  if (!rawUploadId || busy || entries.length === 0) return false;
  return entries.every(
    (entry) => entry.upload?.validation_status === "valid" && entry.description.trim().length > 0 && !entry.error,
  );
}
