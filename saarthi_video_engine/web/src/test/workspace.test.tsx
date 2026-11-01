import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProcessReview } from "../components/ProcessReview";
import { getMusicLibrary, getThumbnail, uploadPreviewUrl } from "../lib/api";
import type { EditingPlanDocument, SessionReceipt, SessionStatusBody, StudioSettings } from "../types";

vi.mock("../lib/api", () => ({
  getThumbnail: vi.fn(),
  savePlanEdits: vi.fn(),
  undoPlanChange: vi.fn(),
  getMusicLibrary: vi.fn(),
  musicPreviewUrl: vi.fn(),
  uploadMusic: vi.fn(),
  uploadPreviewUrl: vi.fn(),
  assistantPropose: vi.fn(),
  assistantApply: vi.fn(),
}));

const effects = Object.fromEntries(
  ["glitter", "sparkle", "cinematic", "warm", "cool", "black_and_white", "contrast", "saturation", "brightness", "vignette", "background_blur", "zoom"].map((name) => [name, { enabled: false, intensity: 0.4, scope: name === "zoom" ? "teacher" : "all" }]),
) as StudioSettings["effects"];

const settings: StudioSettings = {
  version: 1,
  framing: { mode: "balanced", teacher_height: 0.62, focus_x: null, focus_y: 0.4 },
  broll: { fit: "cover" },
  transitions: { style: "cut", seconds: 0.25 },
  captions: { size: 34, position: 0.8, highlight: "#f5c400", color: "#ffffff", style: "box", max_words: 3, uppercase: true },
  music: { source: "default", track_id: null, upload_id: null, volume: 1, under_speech: 0.12, in_pauses: 0.28, fade_in: 0.5, fade_out: 1.5, ducking: true, into_outro: true },
  effects,
  outro: { fill: "blur" },
  intro_seconds: 3,
};

const receipt: SessionReceipt = {
  session_id: "s1",
  mode: "my_broll",
  status: "plan_ready",
  raw_video: { upload_id: "raw-1", filename: "VIDEO_2.mp4", size_bytes: 1, content_type: "video/mp4", duration_seconds: 41.6, width: 1080, height: 1920, frame_rate: 30, validation_status: "valid" },
  broll: [],
  broll_count: 3,
  total_bytes: 1,
  note: "",
  created_at: "",
} as unknown as SessionReceipt;

const plan: EditingPlanDocument = {
  session_id: "s1",
  mode: "my_broll",
  status: "plan_ready",
  raw_video: { upload_id: "raw-1", filename: "VIDEO_2.mp4", duration_seconds: 41.6 },
  transcript: { segment_count: 11 },
  picture_decisions: [],
  placements: [],
  rejected_placements: [],
  evaluations: [],
  catalogue: [],
  plan_version: { version_id: "v001-a", number: 1, parent_version_id: null, origin: "generated" },
  broll_cap: 0.58,
  validation: { ok: true, errors: [] },
  rendered: false,
  settings,
};

function props(status: SessionStatusBody, withPlan: boolean) {
  return {
    receipt,
    status,
    transcript: null,
    plan: withPlan ? plan : null,
    error: null,
    starting: false,
    onStart: () => undefined,
    onRetry: () => undefined,
    onReset: () => undefined,
    render: null,
    renderStarting: false,
    renderError: null,
    videoUrl: null,
    onRender: () => undefined,
    onPlanSaved: () => undefined,
  };
}

beforeEach(() => {
  vi.mocked(getThumbnail).mockResolvedValue(new Blob(["x"]));
  vi.mocked(getMusicLibrary).mockResolvedValue({ categories: ["inspirational"], tracks: [{ track_id: "t1", title: "Inspire Action", artist: "Yuri Megis", category: "inspirational", duration_seconds: 120, license: "CC BY 4.0", license_url: "", page_url: "", default: true }] });
  vi.mocked(uploadPreviewUrl).mockResolvedValue("blob:raw");
});

describe("editing workspace", () => {
  it("explains why it is locked when processing failed", () => {
    render(<ProcessReview {...props({ session_id: "s1", status: "failed", stage: "failed", error: "x: source interval is past the clip duration" } as SessionStatusBody, false)} />);
    const workspace = screen.getByRole("region", { name: "Editing workspace" });
    expect(workspace).toHaveTextContent("Processing failed, so there is no editing plan to work on yet");
    expect(workspace).toHaveTextContent("background music and your own music, filters and effects, captions and framing, and the AI editing assistant");
  });

  it("shows timeline, look and sound, and the assistant as tabs once the plan is ready", async () => {
    const user = userEvent.setup();
    render(<ProcessReview {...props({ session_id: "s1", status: "plan_ready", stage: "plan_ready" } as SessionStatusBody, true)} />);
    expect(screen.getByRole("tab", { name: "Cutaways & timeline" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("region", { name: "Review B-roll and title" })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Look & sound" }));
    expect(screen.getByRole("radio", { name: "Music library" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Glitter" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Size" })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "AI editing assistant" }));
    expect(screen.getByLabelText("Editing instruction")).toBeVisible();
    expect(screen.queryByRole("radio", { name: "Music library" })).toBeNull();
    await user.click(screen.getByRole("tab", { name: "Cutaways & timeline" }));
    expect(screen.getByRole("region", { name: "Review B-roll and title" })).toBeVisible();
    expect(screen.getByRole("radio", { name: "Music library", hidden: true })).not.toBeVisible();
  });
});
