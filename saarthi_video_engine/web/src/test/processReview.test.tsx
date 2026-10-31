import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ProcessReview } from "../components/ProcessReview";
import type { EditingPlanDocument, RenderStatusBody, SessionReceipt, SessionStatusBody, TranscriptDocument } from "../types";

const receipt: SessionReceipt = {
  session_id: "session-1",
  mode: "my_broll",
  status: "prepared",
  raw_video: {
    upload_id: "raw-1",
    filename: "lesson.mp4",
    size_bytes: 2048,
    content_type: "video/mp4",
    duration_seconds: 8,
    width: 160,
    height: 160,
    validation_status: "valid",
  },
  broll: [
    {
      upload_id: "clip-1",
      filename: "phone.mp4",
      description: "A student watching a lecture on a smartphone.",
      size_bytes: 1000,
      content_type: "video/mp4",
      duration_seconds: 8,
      width: 160,
      height: 160,
      validation_status: "valid",
    },
  ],
  total_bytes: 3048,
  broll_count: 1,
  note: "Files stored.",
  created_at: "2026-10-30T00:00:00Z",
};

const ready: SessionStatusBody = {
  session_id: "session-1",
  mode: "my_broll",
  status: "plan_ready",
  stage: "plan_ready",
  error: "",
  message: "The editing plan is ready for review. No video has been rendered.",
  filename: "lesson.mp4",
  broll_count: 1,
  counts: {
    segments: 2,
    broll_supplied: 1,
    candidate_matches: 1,
    approved_placements: 1,
    teacher_intervals: 1,
    validation: "passed",
  },
};

const transcript: TranscriptDocument = {
  language: "en",
  method: "elevenlabs",
  model: "scribe_v2",
  processed_at: "2026-10-30T00:00:00Z",
  source_sha256: "abc",
  reused: false,
  translated_to_english: false,
  segments: [
    {
      id: "a0",
      text: "main hospital ja raha hoon",
      original_text: "main hospital ja raha hoon",
      start: 0.2,
      end: 4,
      words: [],
    },
  ],
};

const plan: EditingPlanDocument = {
  session_id: "session-1",
  mode: "my_broll",
  status: "plan_ready",
  raw_video: { upload_id: "raw-1", filename: "lesson.mp4", duration_seconds: 8 },
  transcript: { segment_count: 1, model: "scribe_v2", provider: "elevenlabs" },
  picture_decisions: [
    {
      decision_id: "broll-1",
      picture_role: "show_broll",
      start: 0.2,
      end: 4,
      reason: "matched",
      filename: "phone.mp4",
      description: "A student watching a lecture on a smartphone.",
      speech_text: "main hospital ja raha hoon",
      source_in: 0,
      source_out: 3.8,
      score: 0.8,
      explanation: "The description shares words with this speech.",
      origin: "user",
      evidence: "description",
    },
    { decision_id: "teacher-1", picture_role: "show_teacher", start: 4, end: 8, reason: "teacher" },
  ],
  validation: { ok: true, errors: [] },
  rendered: false,
};

describe("processing review", () => {
  it("offers processing only after a session is prepared", () => {
    render(
      <ProcessReview
        receipt={receipt}
        status={null}
        transcript={null}
        plan={null}
        error={null}
        starting={false}
        onStart={vi.fn()}
        onRetry={vi.fn()}
        onReset={vi.fn()}
        render={null}
        renderStarting={false}
        renderError={null}
        videoUrl={null}
        onRender={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /start ai processing/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /render/i })).not.toBeInTheDocument();
  });

  it("shows the current stage without a percentage", () => {
    render(
      <ProcessReview
        receipt={receipt}
        status={{ ...ready, status: "transcribing", stage: "transcribing", message: "Transcribing the lesson.", counts: null }}
        transcript={null}
        plan={null}
        error={null}
        starting={false}
        onStart={vi.fn()}
        onRetry={vi.fn()}
        onReset={vi.fn()}
        render={null}
        renderStarting={false}
        renderError={null}
        videoUrl={null}
        onRender={vi.fn()}
      />,
    );
    expect(screen.getByText("Transcribing the lesson.")).toBeInTheDocument();
    expect(screen.getByText("Current")).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("shows a transcript, user footage, and the failure text", async () => {
    const user = userEvent.setup();
    const retry = vi.fn();
    const { rerender } = render(
      <ProcessReview
        receipt={receipt}
        status={ready}
        transcript={transcript}
        plan={plan}
        error={null}
        starting={false}
        onStart={vi.fn()}
        onRetry={retry}
        onReset={vi.fn()}
        render={null}
        renderStarting={false}
        renderError={null}
        videoUrl={null}
        onRender={vi.fn()}
      />,
    );
    expect(screen.getByText("main hospital ja raha hoon")).toBeInTheDocument();
    expect(screen.getByText("Your footage")).toBeInTheDocument();
    expect(screen.getByText("phone.mp4")).toBeInTheDocument();
    expect(screen.getByText(/relevance/i)).toBeInTheDocument();
    expect(screen.getByText("0.80")).toBeInTheDocument();
    expect(screen.getByText(/every cutaway in this plan comes from your uploaded footage/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /render video/i })).toBeInTheDocument();
    expect(screen.getByText("Validation passed.")).toBeInTheDocument();

    rerender(
      <ProcessReview
        receipt={receipt}
        status={{ ...ready, status: "failed", stage: "failed", error: "Transcription failed.", counts: null, message: "Processing failed. No video was rendered." }}
        transcript={null}
        plan={null}
        error={null}
        starting={false}
        onStart={vi.fn()}
        onRetry={retry}
        onReset={vi.fn()}
        render={null}
        renderStarting={false}
        renderError={null}
        videoUrl={null}
        onRender={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Transcription failed.");
    await user.click(screen.getByRole("button", { name: /retry processing/i }));
    expect(retry).toHaveBeenCalledOnce();
  });
});

function reviewWith(renderState: RenderStatusBody | null, onRender = vi.fn(), videoUrl: string | null = null) {
  return render(
    <ProcessReview
      receipt={receipt}
      status={ready}
      transcript={transcript}
      plan={plan}
      error={null}
      starting={false}
      onStart={vi.fn()}
      onRetry={vi.fn()}
      onReset={vi.fn()}
      render={renderState}
      renderStarting={false}
      renderError={null}
      videoUrl={videoUrl}
      onRender={onRender}
    />,
  );
}

const finished: RenderStatusBody = {
  session_id: "session-1",
  render_id: "9f1c2d3e-0000-4000-8000-000000000001",
  status: "completed",
  stage: "completed",
  message: "Video rendered and verified.",
  metadata: { duration_seconds: 66.1, width: 720, height: 1280, frame_rate: 30, size_bytes: 1000 },
  audio_verification: { status: "passed", correlation: 0.981, lag_seconds: 0.02, method: "pearson" },
  outro: { status: "assembled", duration_seconds: 10.8 },
};

describe("rendering", () => {
  it("asks for confirmation with the plan summary before rendering", async () => {
    const user = userEvent.setup();
    const onRender = vi.fn();
    reviewWith({ session_id: "session-1", status: "not_started", stage: "not_started" }, onRender);
    await user.click(screen.getByRole("button", { name: "Render Video" }));
    expect(onRender).not.toHaveBeenCalled();
    const summary = screen.getByRole("region", { name: /confirm render/i });
    expect(summary).toHaveTextContent("Edit with My B-Roll");
    expect(summary).toHaveTextContent("lesson.mp4");
    expect(summary).toHaveTextContent("8.0s");
    expect(summary).toHaveTextContent("Approved B-roll placements1");
    expect(summary).toHaveTextContent("Teacher-only intervals1");
    expect(summary).toHaveTextContent("Plan validationPassed");
    await user.click(screen.getByRole("button", { name: /confirm and render/i }));
    expect(onRender).toHaveBeenCalledOnce();
  });

  it("shows the real render stage without a percentage", () => {
    reviewWith({ session_id: "session-1", render_id: "r-1", status: "rendering", stage: "rendering", message: "HyperFrames is rendering the approved timeline." });
    const stages = screen.getByRole("list", { name: /rendering stages/i });
    expect(stages).toHaveTextContent("Rendering with HyperFrames");
    expect(screen.getByText("HyperFrames is rendering the approved timeline.")).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /render video/i })).not.toBeInTheDocument();
  });

  it("shows the finished video, its checks, and a download link", () => {
    reviewWith(finished, vi.fn(), "blob:fixture");
    const panel = screen.getByRole("region", { name: /rendered video/i });
    expect(panel).toHaveTextContent(finished.render_id as string);
    expect(panel).toHaveTextContent("720×1280");
    expect(panel).toHaveTextContent("30 fps");
    expect(panel).toHaveTextContent("Passed · narration correlation 0.981");
    expect(panel).toHaveTextContent("Appended · 10.8s");
    expect(screen.getByLabelText(/rendered video preview/i)).toHaveAttribute("src", "blob:fixture");
    expect(screen.getByRole("link", { name: /download video/i })).toHaveAttribute("download", "saarthi-video-9f1c2d3e.mp4");
  });

  it("shows a failed render with retry and keeps the previous video", async () => {
    const user = userEvent.setup();
    reviewWith(
      { session_id: "session-1", render_id: "r-2", status: "failed", stage: "failed", error: "The HyperFrames render step failed.", last_completed: finished },
      vi.fn(),
      "blob:fixture",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("The HyperFrames render step failed.");
    expect(screen.getByRole("region", { name: /rendered video/i })).toHaveTextContent("Previous video");
    await user.click(screen.getByRole("button", { name: /retry render/i }));
    expect(screen.getByRole("region", { name: /confirm render/i })).toBeInTheDocument();
  });
});
