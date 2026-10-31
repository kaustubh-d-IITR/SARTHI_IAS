import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StudioRoutes } from "../App";
import { BackendProvider } from "../components/BackendStatus";
import { deleteUpload, uploadFile } from "../lib/api";
import type { MediaRecord } from "../types";

vi.mock("../lib/api", () => ({
  getHealth: vi.fn().mockResolvedValue({
    status: "ok",
    version: "1.0.0",
    upload_directory_writable: true,
    configuration: {
      max_file_bytes: 524288000,
      max_session_bytes: 2147483648,
      allowed_extensions: ["mp4", "webm", "mov", "mkv", "m4v"],
      ffprobe_available: true,
      credentials_loaded: false,
    },
  }),
  uploadFile: vi.fn(),
  deleteUpload: vi.fn().mockResolvedValue(undefined),
  createSession: vi.fn(),
  startProcessing: vi.fn(),
  getSessionStatus: vi.fn(),
  getTranscript: vi.fn(),
  getEditingPlan: vi.fn(),
  listSessions: vi.fn().mockResolvedValue({ sessions: [] }),
  getSession: vi.fn(),
  getMe: vi.fn().mockResolvedValue({ mode: "client_header", user: null, csrf_token: null }),
  login: vi.fn(),
  logout: vi.fn(),
}));

const lesson: MediaRecord = {
  upload_id: "raw-1",
  filename: "lesson.mp4",
  size_bytes: 2048,
  content_type: "video/mp4",
  duration_seconds: 12.5,
  width: 720,
  height: 1280,
  frame_rate: 30,
  validation_status: "valid",
};

function renderAt(path: string) {
  return render(
    <BackendProvider>
      <MemoryRouter initialEntries={[path]}>
        <StudioRoutes />
      </MemoryRouter>
    </BackendProvider>,
  );
}

describe("studio screens", () => {
  beforeEach(() => {
    vi.mocked(uploadFile).mockReset();
    vi.mocked(deleteUpload).mockReset();
    vi.mocked(deleteUpload).mockResolvedValue(undefined);
  });

  it("opens each workflow from its card", async () => {
    const user = userEvent.setup();
    renderAt("/");
    expect(screen.getByRole("heading", { name: /turn a raw lesson/i })).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: /start auto-edit/i }));
    expect(screen.getByRole("heading", { name: "AI Auto-Edit" })).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "My B-Roll" }));
    expect(screen.getByRole("heading", { name: "Edit with My B-Roll" })).toBeInTheDocument();
  });

  it("adds and removes b-roll entries and keeps prepare disabled", async () => {
    const user = userEvent.setup();
    renderAt("/my-broll");
    expect(screen.getAllByLabelText(/what this clip shows/i)).toHaveLength(1);
    expect(screen.getByRole("button", { name: /prepare editing session/i })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /add another b-roll/i }));
    expect(screen.getAllByLabelText(/what this clip shows/i)).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: /remove b-roll 2/i }));
    expect(screen.getAllByLabelText(/what this clip shows/i)).toHaveLength(1);
  });

  it("asks for a description before a b-roll file is uploaded", async () => {
    const user = userEvent.setup();
    renderAt("/my-broll");
    const inputs = screen.getAllByLabelText(/drop a b-roll clip/i);
    await user.upload(inputs[0], new File(["clip"], "phone.mp4", { type: "video/mp4" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/add a description/i);
    expect(uploadFile).not.toHaveBeenCalled();
  });

  it("shows upload progress, then an error, and does not claim the video is edited", async () => {
    const user = userEvent.setup();
    let finish: (value: MediaRecord) => void = () => undefined;
    vi.mocked(uploadFile).mockImplementation((_path, _file, _fields, onProgress) => {
      onProgress(40);
      return new Promise<MediaRecord>((resolve) => {
        finish = resolve;
      });
    });
    renderAt("/auto-edit");
    expect(screen.getByText(/does not mean the video has been edited/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /prepare video/i })).toBeDisabled();
    const input = screen.getByLabelText(/drop the raw lesson/i);
    await user.upload(input, new File(["lesson"], "lesson.mp4", { type: "video/mp4" }));
    expect(await screen.findByText("Uploading 40%")).toBeInTheDocument();
    finish(lesson);
    expect(await screen.findByText("lesson.mp4")).toBeInTheDocument();
    expect(screen.getByText(/not edited yet/i)).toBeInTheDocument();

    vi.mocked(uploadFile).mockRejectedValue(new Error("This file could not be read as a video."));
    await user.upload(screen.getByLabelText(/replace the lesson video/i), new File(["bad"], "bad.mp4", { type: "video/mp4" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be read as a video/i);
  });
});
