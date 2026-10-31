import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthGate, AuthProvider } from "../components/Auth";
import { ApiError, getMe, getSession, getSessionStatus, login } from "../lib/api";
import { SessionPage } from "../pages/SessionPage";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    getMe: vi.fn(),
    login: vi.fn(),
    logout: vi.fn(),
    getSession: vi.fn(),
    getSessionStatus: vi.fn(),
  };
});

afterEach(() => vi.clearAllMocks());

describe("sign-in gate", () => {
  it("shows the login screen when there is no session, then the studio after signing in", async () => {
    const user = userEvent.setup();
    vi.mocked(getMe).mockRejectedValue(new ApiError(401, "Sign in to continue."));
    vi.mocked(login).mockRejectedValueOnce(new ApiError(401, "The email or password is incorrect."));
    vi.mocked(login).mockResolvedValueOnce({ mode: "password", user: { id: "u1", email: "manager@example.org", display_name: "Manager" }, csrf_token: "c" });
    render(
      <AuthProvider>
        <AuthGate>
          <p>Studio home</p>
        </AuthGate>
      </AuthProvider>,
    );
    expect(await screen.findByRole("form", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByText("Studio home")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Email"), "manager@example.org");
    await user.type(screen.getByLabelText("Password"), "wrong password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The email or password is incorrect.");
    await user.clear(screen.getByLabelText("Password"));
    await user.type(screen.getByLabelText("Password"), "correct horse battery staple");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Studio home")).toBeInTheDocument();
    expect(login).toHaveBeenLastCalledWith("manager@example.org", "correct horse battery staple");
  });

  it("explains when the API cannot be reached", async () => {
    vi.mocked(getMe).mockRejectedValue(new ApiError(0, "Could not reach the studio API."));
    render(
      <AuthProvider>
        <AuthGate>
          <p>Studio home</p>
        </AuthGate>
      </AuthProvider>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("The studio is not reachable");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("returns to the login screen when the server reports the sign-in has ended", async () => {
    vi.mocked(getMe).mockResolvedValue({ mode: "password", user: { id: "u1", email: "m@example.org", display_name: "" }, csrf_token: "c" });
    render(
      <AuthProvider>
        <AuthGate>
          <p>Studio home</p>
        </AuthGate>
      </AuthProvider>,
    );
    expect(await screen.findByText("Studio home")).toBeInTheDocument();
    window.dispatchEvent(new CustomEvent("studio:signed-out"));
    expect(await screen.findByRole("form", { name: "Sign in" })).toBeInTheDocument();
  });
});

describe("session recovery", () => {
  it("reopens a session from its URL and shows where processing stopped", async () => {
    vi.mocked(getSession).mockResolvedValue({
      session_id: "s1",
      mode: "auto_edit",
      status: "failed",
      raw_video: { upload_id: "r1", filename: "lesson.mp4", size_bytes: 10, content_type: "video/mp4", duration_seconds: 40, width: 720, height: 1280, validation_status: "valid" },
      broll: [],
      total_bytes: 10,
      broll_count: 0,
      note: "",
      created_at: "",
    });
    vi.mocked(getSessionStatus).mockResolvedValue({
      session_id: "s1",
      mode: "auto_edit",
      status: "failed",
      stage: "failed",
      error: "Processing stopped because the worker shut down. Retry to start again.",
      message: "Processing failed. No video was rendered.",
      filename: "lesson.mp4",
      broll_count: 0,
      counts: null,
    });
    render(
      <MemoryRouter initialEntries={["/sessions/s1"]}>
        <Routes>
          <Route path="/sessions/:sessionId" element={<SessionPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("worker shut down");
    expect(screen.getByRole("button", { name: /retry processing/i })).toBeInTheDocument();
    expect(getSession).toHaveBeenCalledWith("s1");
  });

  it("shows a clear message for a session that does not exist", async () => {
    vi.mocked(getSession).mockRejectedValue(new ApiError(404, "That session was not found."));
    render(
      <MemoryRouter initialEntries={["/sessions/missing"]}>
        <Routes>
          <Route path="/sessions/:sessionId" element={<SessionPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("That session was not found.");
  });
});

describe("request headers", () => {
  it("sends the CSRF token and a fresh idempotency key on state-changing requests", async () => {
    const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
    const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ session_id: "s1" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    actual.setCsrfToken("csrf-123");
    await actual.startRender("s1");
    await actual.startRender("s1");
    const [url, first] = fetchMock.mock.calls[0];
    const second = fetchMock.mock.calls[1][1];
    expect(url).toBe("/api/sessions/s1/render");
    expect(first.credentials).toBe("include");
    expect(first.headers["X-CSRF-Token"]).toBe("csrf-123");
    expect(first.headers["Idempotency-Key"]).toBeTruthy();
    expect(first.headers["Idempotency-Key"]).not.toBe(second.headers["Idempotency-Key"]);
    await actual.getSessionStatus("s1").catch(() => undefined);
    expect(fetchMock.mock.calls[2][1].headers["X-CSRF-Token"]).toBeUndefined();
    vi.unstubAllGlobals();
    actual.setCsrfToken(null);
  });
});
