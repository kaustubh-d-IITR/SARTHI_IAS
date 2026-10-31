export type HealthResponse = {
  status: "ok" | "degraded";
  version: string;
  upload_directory_writable: boolean;
  configuration: {
    max_file_bytes: number;
    max_session_bytes: number;
    allowed_extensions: string[];
    ffprobe_available: boolean;
    credentials_loaded: boolean;
    auth_mode?: "client_header" | "password";
    upload_mode?: string;
    environment?: string;
  };
};

export type MediaRecord = {
  upload_id: string;
  asset_id?: string;
  filename: string;
  description?: string;
  size_bytes: number;
  content_type: string;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  frame_rate?: number | null;
  validation_status: "valid";
};

export type SessionStatus =
  | "prepared"
  | "queued"
  | "transcribing"
  | "analysing"
  | "analysing_footage"
  | "selecting_footage"
  | "building_plan"
  | "validating_plan"
  | "plan_ready"
  | "failed";

export type RenderStatus = "not_started" | "render_queued" | "preparing" | "composing" | "rendering" | "verifying" | "assembling_outro" | "completed" | "failed";
export type RenderStatusBody = {
  session_id?: string;
  render_id?: string;
  status: RenderStatus;
  stage: string;
  message?: string;
  error?: string;
  created_at?: string;
  completed_at?: string;
  metadata?: { duration_seconds: number; width: number; height: number; frame_rate: number; size_bytes: number; video_codec?: string; audio_codec?: string };
  audio_verification?: {
    status: string;
    correlation: number;
    lag_seconds: number;
    lesson_render_correlation?: number;
    floor?: number;
    method: string;
  };
  outro?: { status: string; duration_seconds?: number; expected_duration?: number; audio_policy?: string };
  last_completed?: RenderStatusBody;
  plan_version_id?: string;
  title?: { text: string; status: string };
};

export type TitleSuggestion = { text: string; status: "ai_suggestion"; basis: string };

export type TitleState = {
  text: string;
  status: "user_confirmed" | "none" | string;
  source?: string;
  suggestion?: TitleSuggestion | null;
};

export type SessionReceipt = {
  session_id: string;
  mode: "auto_edit" | "my_broll";
  status: SessionStatus;
  stage?: string;
  error?: string;
  raw_video: MediaRecord;
  broll: MediaRecord[];
  total_bytes: number;
  broll_count: number;
  note: string;
  created_at: string;
  render?: RenderStatusBody | null;
  title?: TitleState | null;
  plan_version?: string | null;
};

export type ProcessingCounts = {
  segments: number;
  broll_supplied: number;
  candidate_matches: number;
  approved_placements: number;
  teacher_intervals: number;
  validation: string;
};

export type SessionStatusBody = {
  session_id: string;
  mode: "auto_edit" | "my_broll";
  status: SessionStatus;
  stage: string;
  error: string;
  message: string;
  filename: string;
  broll_count: number;
  counts: ProcessingCounts | null;
  title?: TitleState | null;
  plan_version?: string | null;
  rematch_error?: string;
};

export type TranscriptWord = {
  text: string;
  start: number;
  end: number;
  logprob?: number | null;
};

export type TranscriptSegment = {
  id: string;
  text: string;
  original_text: string;
  start: number;
  end: number;
  timing_precision?: string | null;
  confidence?: number | null;
  words: TranscriptWord[];
};

export type TranscriptDocument = {
  language: string | null;
  method: string | null;
  model: string | null;
  processed_at: string | null;
  source_sha256: string | null;
  reused: boolean;
  translated_to_english: boolean | null;
  segments: TranscriptSegment[];
};

export type PictureDecision = {
  decision_id: string;
  picture_role: "show_teacher" | "show_broll" | string;
  start: number;
  end: number;
  reason: string;
  asset_id?: string;
  filename?: string;
  description?: string;
  speech_text?: string;
  speech_start?: number;
  speech_end?: number;
  source_in?: number;
  source_out?: number;
  score?: number | null;
  explanation?: string;
  evidence?: string;
  approval?: string;
  origin?: string;
  visual_summary?: string;
  description_relevance?: number | null;
  visual_relevance?: number | null;
  confidence?: number | null;
  approved_by?: string;
};

export type Placement = {
  asset_id: string;
  filename: string;
  description: string;
  visual_summary?: string;
  segment_ids?: string[];
  speech_text?: string;
  speech_start: number;
  speech_end: number;
  source_in: number;
  source_out: number;
  description_relevance?: number | null;
  visual_relevance?: number | null;
  score?: number | null;
  confidence?: number | null;
  explanation?: string;
  evidence?: string;
  evidence_sources?: string[];
  approval: "approved" | "rejected" | string;
  approved_by?: string;
  origin?: string;
  clip_duration?: number;
  license?: string;
  page_url?: string;
  trimmed?: string;
  reason?: string;
};

export type FrameAnalysis = {
  timestamp: number | null;
  frame: string;
  description: string;
  subjects: string[];
  actions: string[];
  confidence: number;
};

export type VisualAnalysis = {
  performed: boolean;
  provider: string;
  model: string;
  analysed_at?: string;
  reason?: string;
  method?: string;
  summary: string;
  subjects: string[];
  actions: string[];
  confidence?: number;
  frames: FrameAnalysis[];
  thumbnail?: boolean;
  reused_from_cache?: boolean;
};

export type CatalogueItem = {
  asset_id: string;
  filename: string;
  description: string;
  duration_seconds: number | null;
  visual_analysis?: VisualAnalysis | string | null;
  thumbnail?: boolean;
  origin?: string;
};

export type Evaluation = {
  asset_id: string;
  filename: string;
  evidence_mode: string;
  visual_analysis_performed: boolean;
  consistency: number | null;
  contradicted: boolean;
  status: "selected" | "abstained";
  reason: string;
  top_windows: {
    segment_ids: string[];
    speech_text: string;
    start: number;
    end: number;
    description_relevance: number;
    visual_relevance: number | null;
    combined: number;
    accepted: boolean;
    reason: string;
  }[];
};

export type PlanVersionRef = { version_id: string; number: number; parent_version_id: string | null; origin: string };

export type EditingPlanDocument = {
  session_id: string;
  mode: "auto_edit" | "my_broll";
  status: string;
  raw_video: { upload_id: string; filename: string; duration_seconds: number | null };
  transcript: { source_sha256?: string | null; model?: string | null; provider?: string | null; segment_count: number };
  picture_decisions: PictureDecision[];
  placements?: Placement[];
  rejected_placements?: Placement[];
  evaluations?: Evaluation[];
  catalogue?: CatalogueItem[];
  visual_analysis?: { provider: string; model: string; available: boolean; unavailable_reason?: string; performed: number; assets: number; text_model?: string | null; errors?: Record<string, string> };
  title?: TitleState;
  title_decision_text?: string;
  plan_version?: PlanVersionRef;
  broll_cap?: number;
  validation: { ok: boolean; errors: string[] };
  rendered: boolean;
};

export type PlanVersionRow = {
  version_id: string;
  number: number;
  created_at: string;
  parent_version_id: string | null;
  origin: string;
  validation_ok: boolean;
  renders: { render_id: string; status: string }[];
};

export type SessionSummary = {
  session_id: string;
  mode: "auto_edit" | "my_broll";
  status: SessionStatus;
  title: string;
  filename: string;
  created_at: string;
  render_status: string;
  has_video: boolean;
};

export type PlacementEdit = {
  asset_id: string;
  speech_start: number;
  speech_end: number;
  source_in: number;
  source_out: number;
  approval: "approved" | "rejected";
};

export type BrollDraft = {
  key: string;
  description: string;
  file: File | null;
  fileName: string | null;
  fileSize: number | null;
  upload: MediaRecord | null;
  progress: number | null;
  error: string | null;
};
