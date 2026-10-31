import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AssistantPanel } from "../components/AssistantPanel";
import { PlanReview } from "../components/PlanReview";
import { filterCss, StudioControls } from "../components/StudioControls";
import { assistantApply, assistantPropose, getMusicLibrary, getThumbnail, savePlanEdits, undoPlanChange, uploadPreviewUrl } from "../lib/api";
import type { EditingPlanDocument, StudioSettings } from "../types";

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
  ["glitter", "sparkle", "cinematic", "warm", "cool", "black_and_white", "contrast", "saturation", "brightness", "vignette", "background_blur", "zoom"].map((name) => [name, { enabled: false, intensity: 0.4, scope: name === "zoom" ? "teacher" : name === "glitter" || name === "sparkle" ? "all" : "all" }]),
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

const plan: EditingPlanDocument = {
  session_id: "s1",
  mode: "my_broll",
  status: "plan_ready",
  raw_video: { upload_id: "raw-1", filename: "lesson.mp4", duration_seconds: 22.88 },
  transcript: { segment_count: 6 },
  picture_decisions: [],
  placements: [
    {
      asset_id: "newspaper",
      filename: "newspaper.mp4",
      description: "A newspaper with a big red cross over it",
      matched_phrase: "naa hi mainne newspaper padha",
      speech_start: 0.12,
      speech_end: 1.26,
      source_in: 0,
      source_out: 1.14,
      confidence: 0.8,
      occurrence: { index: 1, count: 2, terms: ["newspaper"] },
      placement_rule: "back_to_back",
      approval: "approved",
      approved_by: "director",
      clip_duration: 5,
    },
    {
      asset_id: "youtube",
      filename: "phone.mp4",
      description: "A phone opening YouTube",
      matched_phrase: "mainne bhi YouTube khola thaa.",
      speech_start: 6.08,
      speech_end: 8.08,
      source_in: 0,
      source_out: 2,
      confidence: 0.8,
      approval: "approved",
      approved_by: "director",
      clip_duration: 5,
    },
  ],
  rejected_placements: [],
  evaluations: [],
  catalogue: [],
  plan_version: { version_id: "v002-x", number: 2, parent_version_id: "v001-a", origin: "manual_edit" },
  broll_cap: 0.58,
  validation: { ok: true, errors: [] },
  rendered: false,
  settings,
};

beforeEach(() => {
  vi.mocked(getThumbnail).mockResolvedValue(new Blob(["x"]));
  vi.mocked(savePlanEdits).mockReset();
  vi.mocked(savePlanEdits).mockResolvedValue(plan);
  vi.mocked(undoPlanChange).mockReset();
  vi.mocked(undoPlanChange).mockResolvedValue(plan);
  vi.mocked(getMusicLibrary).mockResolvedValue({
    categories: ["inspirational", "calm_educational"],
    tracks: [
      { track_id: "default-inspire-action", title: "Inspire Action", artist: "Yuri Megis", category: "inspirational", duration_seconds: 120, license: "CC BY 4.0", license_url: "", page_url: "https://commons.wikimedia.org/", default: true },
      { track_id: "calm-1", title: "Calm Piano", artist: "Someone", category: "calm_educational", duration_seconds: 90, license: "CC0", license_url: "", page_url: "https://commons.wikimedia.org/", default: false },
    ],
  });
  vi.mocked(uploadPreviewUrl).mockResolvedValue("blob:raw");
  globalThis.URL.createObjectURL = vi.fn(() => "blob:thumb");
  globalThis.URL.revokeObjectURL = vi.fn();
});

describe("plan review timeline", () => {
  it("shows where each clip was placed and saves a timeline move as a real edit", async () => {
    const user = userEvent.setup();
    render(<PlanReview sessionId="s1" plan={plan} disabled={false} onSaved={() => undefined} />);
    expect(screen.getByText("“naa hi mainne newspaper padha”")).toBeInTheDocument();
    expect(screen.getByText(/mention 1 of 2/)).toBeInTheDocument();
    const block = screen.getByRole("button", { name: /phone.mp4 from 6.08 to 8.08 seconds/ });
    block.focus();
    await user.keyboard("{ArrowRight}{ArrowRight}");
    await user.click(screen.getByRole("button", { name: "Save changes and validate" }));
    await waitFor(() => expect(savePlanEdits).toHaveBeenCalled());
    const payload = vi.mocked(savePlanEdits).mock.calls[0][1];
    const moved = payload.placements.find((row) => row.asset_id === "youtube")!;
    expect(moved.speech_start).toBeCloseTo(6.28);
    expect(moved.speech_end - moved.speech_start).toBeCloseTo(2);
    expect(moved.source_out - moved.source_in).toBeCloseTo(2);
  });

  it("undoes the last change through the API", async () => {
    const user = userEvent.setup();
    render(<PlanReview sessionId="s1" plan={plan} disabled={false} onSaved={() => undefined} />);
    await user.click(screen.getByRole("button", { name: "Undo last change" }));
    await waitFor(() => expect(undoPlanChange).toHaveBeenCalledWith("s1", "v002-x"));
  });
});

describe("look and sound", () => {
  it("previews with the renderer's own filter values", () => {
    const warm = { ...settings, effects: { ...settings.effects, warm: { enabled: true, intensity: 0.5, scope: "all" } } };
    expect(filterCss(warm, "all")).toBe("sepia(0.150) saturate(1.075) hue-rotate(-4.0deg)");
    expect(filterCss(warm, "teacher")).toBe("");
  });

  it("saves only the changed settings with the unchanged placements", async () => {
    const user = userEvent.setup();
    render(<StudioControls sessionId="s1" plan={plan} disabled={false} onSaved={() => undefined} />);
    await user.click(screen.getByRole("checkbox", { name: "Glitter" }));
    const glitterScope = await screen.findAllByRole("combobox");
    await user.selectOptions(glitterScope[glitterScope.length - 1], "intro");
    await user.click(screen.getByRole("radio", { name: "Music library" }));
    await user.click(await screen.findByRole("radio", { name: "Use Calm Piano" }));
    await user.click(screen.getByRole("button", { name: "Save look and sound" }));
    await waitFor(() => expect(savePlanEdits).toHaveBeenCalled());
    const payload = vi.mocked(savePlanEdits).mock.calls[0][1];
    expect(payload.settings).toEqual({ music: { source: "library", track_id: "calm-1" }, effects: { glitter: { enabled: true, intensity: 0.4, scope: "intro" } } });
    expect(payload.placements.map((row) => [row.asset_id, row.speech_start])).toEqual([
      ["newspaper", 0.12],
      ["youtube", 6.08],
    ]);
  });
});

describe("editing assistant", () => {
  it("shows what it understood and applies only after a click", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    vi.mocked(assistantPropose).mockResolvedValue({
      status: "proposal",
      understood: "I will place “A newspaper with a big red cross over it” at 0.12 s, on “naa hi mainne newspaper padha”, for 2 s.",
      operations: [{ operation: "move_broll", asset_id: "newspaper", target_phrase: "na hi mene newspaper padha", matched_phrase: "naa hi mainne newspaper padha", target_start: 0.12, duration_seconds: 2 }],
      base_version_id: "v002-x",
    });
    vi.mocked(assistantApply).mockResolvedValue(plan);
    render(<AssistantPanel sessionId="s1" plan={plan} disabled={false} onSaved={onSaved} />);
    await user.click(screen.getByRole("button", { name: "Open assistant" }));
    await user.type(screen.getByLabelText("Editing instruction"), "Move the newspaper B-roll to where sir says na hi mene newspaper padha and keep it for 2 seconds.");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/at 0.12 s, on “naa hi mainne newspaper padha”/)).toBeInTheDocument();
    expect(assistantApply).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(assistantApply).toHaveBeenCalledWith("s1", "v002-x", expect.arrayContaining([expect.objectContaining({ operation: "move_broll", asset_id: "newspaper" })])));
    expect(onSaved).toHaveBeenCalledWith(plan);
    expect(await screen.findByText("Applied as a new plan version.")).toBeInTheDocument();
  });

  it("asks back when it is unsure and applies nothing", async () => {
    const user = userEvent.setup();
    vi.mocked(assistantPropose).mockResolvedValue({ status: "clarify", question: "Which clip should I change?", operations: [] });
    vi.mocked(assistantApply).mockReset();
    render(<AssistantPanel sessionId="s1" plan={plan} disabled={false} onSaved={() => undefined} />);
    await user.click(screen.getByRole("button", { name: "Open assistant" }));
    await user.type(screen.getByLabelText("Editing instruction"), "Is B-roll ko hata do");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Which clip should I change?")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
    expect(assistantApply).not.toHaveBeenCalled();
  });
});
