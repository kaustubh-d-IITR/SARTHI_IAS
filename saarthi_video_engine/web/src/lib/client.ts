const STORAGE_KEY = "sarthi.studio.clientId";

export function clientId(): string {
  const existing = window.localStorage.getItem(STORAGE_KEY);
  if (existing) return existing;
  const created = window.crypto.randomUUID();
  window.localStorage.setItem(STORAGE_KEY, created);
  return created;
}
