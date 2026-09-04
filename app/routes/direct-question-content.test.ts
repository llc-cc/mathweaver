import { describe, expect, it } from "vitest";
import {
  buildDirectImageMarkdown,
  directQuestionEditorKey,
  findDirectQuestionImageDeletionRange,
  insertDirectTextAtSelection,
  isDirectInlineImageDataUrl,
  serializeDirectQuestionContentSegments,
  shouldSyncDirectQuestionEditorInput,
  splitDirectQuestionContent,
} from "./direct-question-content";

const IMAGE = "data:image/png;base64,AAECAw==";

describe("direct question inline images", () => {
  it("uses a distinct editor instance for every question field", () => {
    expect(directQuestionEditorKey("question-1", "question")).not.toBe(directQuestionEditorKey("question-2", "question"));
    expect(directQuestionEditorKey("question-1", "question")).not.toBe(directQuestionEditorKey("question-1", "referenceAnswer"));
  });

  it("does not sync controlled content while an IME composition is active", () => {
    expect(shouldSyncDirectQuestionEditorInput(true, false)).toBe(false);
    expect(shouldSyncDirectQuestionEditorInput(false, true)).toBe(false);
    expect(shouldSyncDirectQuestionEditorInput(false, false)).toBe(true);
  });

  it("builds a safe markdown image from a data URL", () => {
    expect(buildDirectImageMarkdown("题目[1].png", IMAGE)).toBe(`![题目 1 .png](${IMAGE})`);
    expect(isDirectInlineImageDataUrl(IMAGE)).toBe(true);
    expect(isDirectInlineImageDataUrl("data:text/plain;base64,AA==")).toBe(false);
  });

  it("splits valid images while leaving invalid data as text", () => {
    expect(splitDirectQuestionContent(`前文\n![图示](${IMAGE})\n后文`)).toEqual([
      { type: "text", value: "前文\n" },
      { type: "image", alt: "图示", src: IMAGE },
      { type: "text", value: "\n后文" },
    ]);
    expect(splitDirectQuestionContent("![图示](https://example.com/a.png)")).toEqual([
      { type: "text", value: "![图示](https://example.com/a.png)" },
    ]);
  });

  it("round-trips mixed text and image content without exposing the data URL as text", () => {
    const content = `题目前\n![图示](${IMAGE})\n题目后`;
    expect(serializeDirectQuestionContentSegments(splitDirectQuestionContent(content))).toBe(content);
  });

  it("inserts an image marker at the selected range", () => {
    expect(insertDirectTextAtSelection("题目前题目后", 3, 5, `![图](${IMAGE})`)).toEqual({
      text: `题目前![图](${IMAGE})后`,
      selectionStart: 3 + `![图](${IMAGE})`.length,
      selectionEnd: 3 + `![图](${IMAGE})`.length,
    });
  });

  it("removes an image marker when its complete source range is selected", () => {
    const marker = buildDirectImageMarkdown("图.png", IMAGE);
    expect(insertDirectTextAtSelection(`前${marker}后`, 1, 1 + marker.length, "").text).toBe("前后");
  });

  it("finds an adjacent image for keyboard deletion", () => {
    const marker = buildDirectImageMarkdown("图.png", IMAGE);
    const content = `前${marker}后`;
    expect(findDirectQuestionImageDeletionRange(content, { start: 1 + marker.length, end: 1 + marker.length }, "Backspace"))
      .toEqual({ start: 1, end: 1 + marker.length });
    expect(findDirectQuestionImageDeletionRange(content, { start: 1, end: 1 }, "Delete"))
      .toEqual({ start: 1, end: 1 + marker.length });
    expect(findDirectQuestionImageDeletionRange(content, { start: 0, end: 1 }, "Delete")).toBeNull();
  });
});
