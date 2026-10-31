import { clientId } from "./client";
import type {
  AssistantOperation,
  AssistantReply,
  EditingPlanDocument,
  MusicTrack,
  SettingsChange,
  HealthResponse,
  MediaRecord,
  PlacementEdit,
  PlanVersionRow,
  RenderStatusBody,
  SessionReceipt,
  SessionStatusBody,
  SessionSummary,
  TranscriptDocument,
} from "../types";

const base = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
let csrfToken: string | null = null;

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

function notifySignedOut(): void {
  window.dispatchEvent(new CustomEvent("studio:signed-out"));
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { error?: { message?: string } };
    return body.error?.message || "The studio could not complete that request.";
  } catch {
    return "The studio could not complete that request.";
  }
}

function headers(extra: Record<string, string> = {}, unsafe = false): Record<string, string> {
  const result: Record<string, string> = { "X-Studio-Client": clientId(), ...extra };
  if (unsafe && csrfToken) result["X-CSRF-Token"] = csrfToken;
  return result;
}

async function request<T>(path: string, init: { method?: string; body?: unknown; idempotent?: boolean } = {}): Promise<T> {
  const method = init.method || "GET";
  const unsafe = method !== "GET";
  const extra: Record<string, string> = {};
  if (init.body !== undefined) extra["Content-Type"] = "application/json";
  if (init.idempotent) extra["Idempotency-Key"] = window.crypto.randomUUID();
  let response: Response;
  try {
    response = await fetch(`${base}${path}`, {
      method,
      credentials: "include",
      headers: headers(extra, unsafe),
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
    });
  } catch {
    throw new ApiError(0, "Could not reach the studio API. Check your connection and try again.");
  }
  if (response.status === 401 && !path.startsWith("/api/auth/")) notifySignedOut();
  if (!response.ok) throw new ApiError(response.status, await readError(response));
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health");
}

export type AuthState = { mode: "client_header" | "password"; user: { id: string; email: string; display_name: string } | null; csrf_token: string | null };

export async function getMe(): Promise<AuthState> {
  const state = await request<AuthState>("/api/auth/me");
  setCsrfToken(state.csrf_token);
  return state;
}

export async function login(email: string, password: string): Promise<AuthState> {
  const body = await request<{ user: AuthState["user"]; csrf_token: string }>("/api/auth/login", { method: "POST", body: { email, password } });
  setCsrfToken(body.csrf_token);
  return { mode: "password", user: body.user, csrf_token: body.csrf_token };
}

export async function logout(): Promise<void> {
  try {
    await request("/api/auth/logout", { method: "POST" });
  } finally {
    setCsrfToken(null);
  }
}

function putWithProgress(url: string, file: File, extraHeaders: Record<string, string>, onProgress: (percent: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url.startsWith("http") ? url : `${base}${url}`);
    for (const [key, value] of Object.entries(extraHeaders)) xhr.setRequestHeader(key, value);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.max(1, Math.min(99, Math.round((event.loaded / event.total) * 100))));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
        return;
      }
      let message = "The upload was interrupted. Try again.";
      try {
        message = (JSON.parse(xhr.responseText || "{}") as { error?: { message?: string } }).error?.message || message;
      } catch {
        // storage services answer in XML; the generic message is clearer for the user
      }
      reject(new ApiError(xhr.status, message));
    };
    xhr.onerror = () => reject(new ApiError(0, "The upload could not reach storage. Check your connection and try again."));
    xhr.send(file);
  });
}

/** Direct upload: reserve, send the file straight to storage, then let the server check it. */
export async function uploadFile(
  path: "/api/uploads/raw-video" | "/api/uploads/broll",
  file: File,
  fields: Record<string, string>,
  onProgress: (percent: number) => void,
): Promise<MediaRecord> {
  const kind = path.endsWith("broll") ? "broll" : "raw";
  const target = await request<{ upload_id: string; upload: { url: string; headers: Record<string, string> } }>("/api/uploads/initiate", {
    method: "POST",
    body: { kind, filename: file.name, size_bytes: file.size, description: fields.description },
  });
  onProgress(1);
  await putWithProgress(target.upload.url, file, target.upload.headers, onProgress);
  const record = await request<MediaRecord>(`/api/uploads/${target.upload_id}/complete`, { method: "POST" });
  onProgress(100);
  return record;
}

export async function deleteUpload(uploadId: string): Promise<void> {
  await request(`/api/uploads/${uploadId}`, { method: "DELETE" });
}

export async function createSession(payload: {
  mode: "auto_edit" | "my_broll";
  raw_video_id: string;
  broll: { asset_id: string; description: string }[];
  title?: string;
}): Promise<SessionReceipt> {
  return request<SessionReceipt>("/api/sessions", { method: "POST", body: payload });
}

export function listSessions(): Promise<{ sessions: SessionSummary[] }> {
  return request("/api/sessions");
}

export function getSession(sessionId: string): Promise<SessionReceipt> {
  return request<SessionReceipt>(`/api/sessions/${sessionId}`);
}

export function startProcessing(sessionId: string): Promise<SessionReceipt> {
  return request<SessionReceipt>(`/api/sessions/${sessionId}/process`, { method: "POST", idempotent: true });
}

export function getSessionStatus(sessionId: string): Promise<SessionStatusBody> {
  return request<SessionStatusBody>(`/api/sessions/${sessionId}/status`);
}

export function getTranscript(sessionId: string): Promise<TranscriptDocument> {
  return request<TranscriptDocument>(`/api/sessions/${sessionId}/transcript`);
}

export function getEditingPlan(sessionId: string): Promise<EditingPlanDocument> {
  return request<EditingPlanDocument>(`/api/sessions/${sessionId}/editing-plan`);
}

export function startRender(sessionId: string): Promise<SessionReceipt> {
  return request<SessionReceipt>(`/api/sessions/${sessionId}/render`, { method: "POST", idempotent: true });
}

export function getRenderStatus(sessionId: string): Promise<RenderStatusBody> {
  return request<RenderStatusBody>(`/api/sessions/${sessionId}/render-status`);
}

export type VideoLinks = { render_id: string; url: string; download_url: string; expires_in: number };

/** Short-lived signed links to the newest completed video. Fetch again when they expire. */
export function getVideoLinks(sessionId: string): Promise<VideoLinks> {
  return request<VideoLinks>(`/api/sessions/${sessionId}/video-url`);
}

export function absoluteUrl(url: string): string {
  return url.startsWith("http") ? url : `${base}${url}`;
}

export async function getThumbnail(sessionId: string, assetId: string): Promise<Blob> {
  let response: Response;
  try {
    response = await fetch(`${base}/api/sessions/${sessionId}/assets/${assetId}/thumbnail`, { credentials: "include", headers: headers() });
  } catch {
    throw new ApiError(0, "Could not reach the studio API.");
  }
  if (!response.ok) throw new ApiError(response.status, await readError(response));
  return response.blob();
}

export function savePlanEdits(
  sessionId: string,
  payload: { base_version_id: string; placements: PlacementEdit[]; title?: string; change_title?: boolean; settings?: SettingsChange },
): Promise<EditingPlanDocument> {
  return request<EditingPlanDocument>(`/api/sessions/${sessionId}/editing-plan/edits`, { method: "POST", body: payload });
}

export function getMusicLibrary(): Promise<{ categories: string[]; tracks: MusicTrack[] }> {
  return request("/api/music/library");
}

/** A library track as a playable blob URL. The request carries the sign-in cookie. */
export async function musicPreviewUrl(trackId: string): Promise<string> {
  let response: Response;
  try {
    response = await fetch(`${base}/api/music/library/${encodeURIComponent(trackId)}/preview`, { credentials: "include", headers: headers() });
  } catch {
    throw new ApiError(0, "Could not reach the studio API.");
  }
  if (!response.ok) throw new ApiError(response.status, await readError(response));
  return URL.createObjectURL(await response.blob());
}

export async function uploadPreviewUrl(uploadId: string): Promise<string> {
  const body = await request<{ url: string }>(`/api/uploads/${uploadId}/preview-url`);
  return absoluteUrl(body.url);
}

export async function uploadMusic(file: File, onProgress: (percent: number) => void): Promise<MediaRecord> {
  const target = await request<{ upload_id: string; upload: { url: string; headers: Record<string, string> } }>("/api/uploads/initiate", {
    method: "POST",
    body: { kind: "music", filename: file.name, size_bytes: file.size },
  });
  onProgress(1);
  await putWithProgress(target.upload.url, file, target.upload.headers, onProgress);
  const record = await request<MediaRecord>(`/api/uploads/${target.upload_id}/complete`, { method: "POST" });
  onProgress(100);
  return record;
}

export function assistantPropose(sessionId: string, message: string): Promise<AssistantReply> {
  return request<AssistantReply>(`/api/sessions/${sessionId}/assistant/propose`, { method: "POST", body: { message } });
}

export function assistantApply(sessionId: string, baseVersionId: string, operations: AssistantOperation[]): Promise<EditingPlanDocument> {
  return request<EditingPlanDocument>(`/api/sessions/${sessionId}/assistant/apply`, { method: "POST", body: { base_version_id: baseVersionId, operations } });
}

export function undoPlanChange(sessionId: string, baseVersionId: string): Promise<EditingPlanDocument> {
  return request<EditingPlanDocument>(`/api/sessions/${sessionId}/assistant/undo`, { method: "POST", body: { base_version_id: baseVersionId } });
}

export function getPlanVersions(sessionId: string): Promise<{ current_version_id: string | null; versions: PlanVersionRow[] }> {
  return request(`/api/sessions/${sessionId}/editing-plan/versions`);
}

export function rematchFootage(sessionId: string): Promise<SessionReceipt> {
  return request<SessionReceipt>(`/api/sessions/${sessionId}/rematch`, { method: "POST", idempotent: true });
}
