import { describe, expect, it } from "vitest";
import {
  DOCUMENT_EXTENSIONS,
  IMAGE_MEDIA_TYPES,
  isDocumentFilename,
  isImageMediaType,
  MAX_DOCUMENTS_PER_CONVERSATION,
  MAX_IMAGES_PER_MESSAGE,
  MAX_UPLOAD_SIZE_BYTES,
} from "./limits";

describe("F3 limits.ts — API-sourced caps (D-F3-X-multi-file-cap-source)", () => {
  describe("constants mirror API source values", () => {
    it("MAX_UPLOAD_SIZE_BYTES matches image_service.MAX_UPLOAD_BYTES = 20MB", () => {
      expect(MAX_UPLOAD_SIZE_BYTES).toBe(20 * 1024 * 1024);
    });

    it("MAX_IMAGES_PER_MESSAGE matches PostMessageRequest.images max_length=4", () => {
      expect(MAX_IMAGES_PER_MESSAGE).toBe(4);
    });

    it("IMAGE_MEDIA_TYPES matches ImageRef.media_type 4-literal union", () => {
      expect(IMAGE_MEDIA_TYPES).toEqual([
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
      ]);
    });

    it("MAX_DOCUMENTS_PER_CONVERSATION is null (D-F3-X-document-conversation-count-cap)", () => {
      // Spec 14 ships no per-conversation document count cap at v0.1.
      // The typed `number | null` shape survives v0.2 without TS surgery.
      expect(MAX_DOCUMENTS_PER_CONVERSATION).toBeNull();
    });

    it("DOCUMENT_EXTENSIONS covers Spec 14's SUPPORTED_EXTENSIONS", () => {
      // Sanity-check a sample from each category in the API dispatch table:
      // stdlib-only (txt/md/csv), code (py/ts), extra-gated (pdf/docx/xlsx).
      const sample = [
        ".txt",
        ".md",
        ".csv",
        ".py",
        ".ts",
        ".pdf",
        ".docx",
        ".xlsx",
      ];
      for (const ext of sample) {
        expect(DOCUMENT_EXTENSIONS).toContain(ext);
      }
    });
  });

  describe("isImageMediaType", () => {
    it.each(IMAGE_MEDIA_TYPES)("returns true for %s", (mediaType) => {
      expect(isImageMediaType(mediaType)).toBe(true);
    });

    it("returns false for application/pdf", () => {
      expect(isImageMediaType("application/pdf")).toBe(false);
    });

    it("returns false for empty string", () => {
      expect(isImageMediaType("")).toBe(false);
    });

    it("returns false for image/svg+xml (not in the 4-literal union)", () => {
      // Spec 13 deliberately excludes SVG — no magic-byte signature for
      // the decompression-bomb guard.
      expect(isImageMediaType("image/svg+xml")).toBe(false);
    });
  });

  describe("isDocumentFilename", () => {
    it("returns true for foo.pdf", () => {
      expect(isDocumentFilename("foo.pdf")).toBe(true);
    });

    it("returns true for report.DOCX (case-insensitive)", () => {
      // Match logic mirrors `_is_document_filename` in routes/uploads.py
      // which lowercases the extension before comparison.
      expect(isDocumentFilename("report.DOCX")).toBe(true);
    });

    it("returns false for foo.png (image, not document)", () => {
      expect(isDocumentFilename("foo.png")).toBe(false);
    });

    it("returns false for empty filename", () => {
      expect(isDocumentFilename("")).toBe(false);
    });

    it("returns false for extensionless filename", () => {
      expect(isDocumentFilename("README")).toBe(false);
    });

    it("returns false for a filename ending in a dot (no extension)", () => {
      expect(isDocumentFilename("foo.")).toBe(false);
    });

    it("returns true for code file extensions (py, ts, rs, go, java, kt, etc.)", () => {
      const codeFiles = [
        "main.py",
        "index.ts",
        "lib.rs",
        "cmd.go",
        "App.java",
        "Composer.kt",
      ];
      for (const f of codeFiles) {
        expect(isDocumentFilename(f)).toBe(true);
      }
    });

    it("handles nested paths by checking only the final extension", () => {
      expect(isDocumentFilename("dir/sub/notes.md")).toBe(true);
    });
  });
});
