import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PlanReview } from "../components/PlanReview";
import { RenderPanel } from "../components/RenderPanel";
import { getThumbnail, savePlanEdits } from "../lib/api";
import type { EditingPlanDocument, RenderStatusBody, SessionReceipt } from "../types";

vi.mock("../lib/api", () => ({
  getThumbnail: vi.fn(),
  savePlanEdits: vi.fn(),
}));

const plan: EditingPlanDocument = {
  session_id: "session-1",
  mode: "my_broll",
  status: "plan_ready",
  raw_video: { upload_id: "raw-1", filename: "lesson.mp4", duration_seconds: 14 },
  transcript: { segment_count: 5 },
  picture_decisions: [],
  placements: [
    {
      asset_id: "clip-ppe",
      filename: "ppe.mp4",
      description: "A health worker taking off a protective gown.",
      visual_summary: "CLIP ranks these fixed labels highest across the sampled frames: a health worker wearing protective gear (0.61)",
      speech_text: "I was on COVID duty wearing a PPE gown and mask.",
      speech_start: 5.2,
      speech_end: 8.0,
      source_in: 1.0,
      source_out: 3.8,
      description_relevance: 0.91,
      visual_relevance: 0.88,
      score: 0.89,
      evidence: "combined",
      explanation: "The sampled frames match this speech.",
      approval: "approved",
      approved_by: "matcher",
      clip_duration: 6,
    },
  ],
  rejected_placements: [],
  evaluations: [
    {
      asset_id: "clip-pen",
      filename: "pen.mp4",
      evidence_mode: "combined",
      visual_analysis_performed: true,
      consistency: -0.09,
      contradicted: true,
      status: "abstained",
      reason: "The sampled frames do not resemble the clip's description.",
      top_windows: [],
    },
  ],
  catalogue: [
    {
      asset_id: "clip-ppe",
      filename: "ppe.mp4",
      description: "A health worker taking off a protective gown.",
      duration_seconds: 6,
      thumbnail: true,
      visual_analysis: { performed: true, provider: "clip", model: "openai/clip-vit-base-patch32", summary: "CLIP ranks these fixed labels highest", subjects: [], actions: [], frames: [] },
    },
    {
      asset_id: "clip-pen",
      filename: "pen.mp4",
      description: "Parliament building",
      duration_seconds: 6,
      thumbnail: false,
      visual_analysis: { performed: true, provider: "clip", model: "x", summary: "pen", subjects: [], actions: [], frames: [] },
    },
  ],
  visual_analysis: { provider: "clip", model: "openai/clip-vit-base-patch32", available: true, performed: 2, assets: 2 },
  title: { text: "", status: "none", suggestion: { text: "UPSC, NCERT and Newspaper", status: "ai_suggestion", basis: "Most frequent topic words in the transcript" } },
  plan_version: { version_id: "v001-abc", number: 1, parent_version_id: null, origin: "generated" },
  broll_cap: 0.58,
  validation: { ok: true, errors: [] },
  rendered: false,
};

describe("plan review", () => {
  beforeEach(() => {
    vi.mocked(getThumbnail).mockReset();
    vi.mocked(getThumbnail).mockResolvedValue(new Blob(["jpeg"], { type: "image/jpeg" }));
    vi.mocked(savePlanEdits).mockReset();
    globalThis.URL.createObjectURL = vi.fn(() => "blob:thumb");
    globalThis.URL.revokeObjectURL = vi.fn();
  });

  it("shows the clip, both descriptions, the evidence, and why other clips were not used", async () => {
    render(<PlanReview sessionId="session-1" plan={plan} disabled={false} onSaved={vi.fn()} />);
    expect(await screen.findByAltText("Frame from ppe.mp4")).toHaveAttribute("src", "blob:thumb");
    expect(screen.getByText(/Your description: A health worker taking off a protective gown/)).toBeInTheDocument();
    expect(screen.getByText(/Visual analysis: CLIP ranks these fixed labels highest/)).toBeInTheDocument();
    expect(screen.getByText(/Frames and description · description 0.91 · visual 0.88 · combined 0.89/)).toBeInTheDocument();
    expect(screen.getByText(/Matched speech: I was on COVID duty/)).toBeInTheDocument();
    expect(screen.getByText(/Proposed by the matcher/)).toBeInTheDocument();
    expect(screen.getByText(/Clips not used \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/do not match the description/)).toBeInTheDocument();
  });

  it("labels the title suggestion and only saves it when the user chooses it", async () => {
    const user = userEvent.setup();
    const saved = { ...plan, plan_version: { version_id: "v002-def", number: 2, parent_version_id: "v001-abc", origin: "manual_edit" } };
    vi.mocked(savePlanEdits).mockResolvedValue(saved);
    const onSaved = vi.fn();
    render(<PlanReview sessionId="session-1" plan={plan} disabled={false} onSaved={onSaved} />);
    expect(screen.getByText("AI suggestion")).toBeInTheDocument();
    expect(screen.getByText(/No title is saved/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save changes and validate/i })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /use suggestion/i }));
    expect(screen.getByLabelText(/video title/i)).toHaveValue("UPSC, NCERT and Newspaper");
    await user.click(screen.getByRole("button", { name: /save changes and validate/i }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    expect(savePlanEdits).toHaveBeenCalledWith("session-1", {
      base_version_id: "v001-abc",
      placements: [{ asset_id: "clip-ppe", speech_start: 5.2, speech_end: 8, source_in: 1, source_out: 3.8, approval: "approved" }],
      title: "UPSC, NCERT and Newspaper",
      change_title: true,
    });
  });

  it("keeps a Hindi title exactly and rejects a placement to restore the teacher", async () => {
    const user = userEvent.setup();
    vi.mocked(savePlanEdits).mockResolvedValue(plan);
    render(<PlanReview sessionId="session-1" plan={plan} disabled={false} onSaved={vi.fn()} />);
    await user.click(screen.getByLabelText(/video title/i));
    await user.paste("यूपीएससी की [[सच्चाई]]");
    await user.click(screen.getByRole("button", { name: /reject — keep teacher/i }));
    expect(screen.getByText(/Rejected — teacher stays/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /save changes and validate/i }));
    const payload = vi.mocked(savePlanEdits).mock.calls[0][1];
    expect(payload.title).toBe("यूपीएससी की [[सच्चाई]]");
    expect(payload.placements[0].approval).toBe("rejected");
  });

  it("moves the source window with the timeline and shows server validation errors", async () => {
    const user = userEvent.setup();
    vi.mocked(savePlanEdits).mockRejectedValue(new Error("Cutaways would cover 9.0 s, over the 58% limit of 8.1 s."));
    render(<PlanReview sessionId="session-1" plan={plan} disabled={false} onSaved={vi.fn()} />);
    const sourceIn = screen.getByLabelText(/source in/i);
    await user.clear(sourceIn);
    await user.type(sourceIn, "2");
    expect(screen.getByText(/4.80s of 6.00s/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /save changes and validate/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("over the 58% limit");
    expect(vi.mocked(savePlanEdits).mock.calls[0][1].placements[0]).toMatchObject({ source_in: 2, source_out: 4.8 });
  });

  it("locks editing while a render runs", () => {
    render(<PlanReview sessionId="session-1" plan={plan} disabled onSaved={vi.fn()} />);
    expect(screen.getByRole("button", { name: /reject — keep teacher/i })).toBeDisabled();
    expect(screen.getByText(/locked while a render is running/)).toBeInTheDocument();
  });
});

const receipt: SessionReceipt = {
  session_id: "session-1",
  mode: "my_broll",
  status: "plan_ready",
  raw_video: { upload_id: "raw-1", filename: "lesson.mp4", size_bytes: 1, content_type: "video/mp4", duration_seconds: 14, width: 360, height: 640, validation_status: "valid" },
  broll: [],
  total_bytes: 1,
  broll_count: 1,
  note: "",
  created_at: "",
};
const counts = { segments: 5, broll_supplied: 2, candidate_matches: 1, approved_placements: 1, teacher_intervals: 2, validation: "passed" };

describe("render panel with plan versions", () => {
  it("shows the title and plan version before rendering", async () => {
    const user = userEvent.setup();
    const titled = { ...plan, title: { text: "From [[NEET]] to [[UPSC]]", status: "user_confirmed" } };
    render(<RenderPanel receipt={receipt} counts={counts} planOk render={null} starting={false} error={null} videoUrl={null} onRender={vi.fn()} plan={titled} />);
    await user.click(screen.getByRole("button", { name: "Render Video" }));
    const summary = screen.getByRole("region", { name: /confirm render/i });
    expect(summary).toHaveTextContent("From [[NEET]] to [[UPSC]]");
    expect(summary).toHaveTextContent("Version 1");
  });

  it("keeps an older video and offers a render of the new version", () => {
    const older: RenderStatusBody = {
      render_id: "r-1",
      status: "completed",
      stage: "completed",
      plan_version_id: "v000-old",
      title: { text: "", status: "none" },
      metadata: { duration_seconds: 22, width: 720, height: 1280, frame_rate: 30, size_bytes: 1 },
      audio_verification: { status: "passed", correlation: 0.97, lag_seconds: 0, method: "x" },
      outro: { status: "assembled", duration_seconds: 8 },
    };
    render(<RenderPanel receipt={receipt} counts={counts} planOk render={older} starting={false} error={null} videoUrl="blob:v" onRender={vi.fn()} plan={plan} />);
    expect(screen.getByRole("region", { name: /rendered video/i })).toHaveTextContent("v000-old");
    expect(screen.getByText(/The plan changed after this video was rendered/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /render the new version/i })).toBeInTheDocument();
  });
});
