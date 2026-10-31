import { describe, expect, it } from "vitest";
import { canPrepareAuto, canPrepareBroll } from "../lib/sessionGate";
import type { BrollDraft } from "../types";

function entry(partial: Partial<BrollDraft>): BrollDraft {
  return {
    key: "clip",
    description: "A student watching a lecture on a smartphone.",
    file: null,
    fileName: "clip.mp4",
    fileSize: 10,
    upload: {
      upload_id: "asset-1",
      asset_id: "asset-1",
      filename: "clip.mp4",
      size_bytes: 10,
      content_type: "video/mp4",
      duration_seconds: 1,
      width: 160,
      height: 160,
      validation_status: "valid",
    },
    progress: null,
    error: null,
    ...partial,
  };
}

describe("session gates", () => {
  it("keeps auto-edit closed until a lesson id exists and the form is idle", () => {
    expect(canPrepareAuto(null, false)).toBe(false);
    expect(canPrepareAuto("lesson", true)).toBe(false);
    expect(canPrepareAuto("lesson", false)).toBe(true);
  });

  it("requires a lesson, a clip, and a description in my-broll mode", () => {
    expect(canPrepareBroll(null, [entry({})], false)).toBe(false);
    expect(canPrepareBroll("lesson", [], false)).toBe(false);
    expect(canPrepareBroll("lesson", [entry({ description: "  " })], false)).toBe(false);
    expect(canPrepareBroll("lesson", [entry({ upload: null })], false)).toBe(false);
    expect(canPrepareBroll("lesson", [entry({})], false)).toBe(true);
  });
});
