/**
 * F3 — API-sourced limits the composer must respect (D-F3-X-multi-file-cap-source).
 *
 * Every constant in this file is a HAND-MIRROR of a backend value with a
 * `// API source:` comment naming the file + line of the authoritative
 * definition. When the backend value changes, search this file by the
 * `API source` comment to find every constant that needs the bump.
 *
 * The `MAX_DOCUMENTS_PER_CONVERSATION` constant is typed `number | null`
 * — at v0.1 Spec 14 ships NO per-conversation document count cap (verified
 * by grep against `document_service.py`); the constant is `null` so the
 * shape survives v0.2 without TypeScript surgery. v0.2 candidate: a
 * `GET /v1/limits` endpoint exposes API constants programmatically.
 *
 * See docs/specs/phase2/spec_F3/decisions.md for D-F3-X-multi-file-cap-source
 * and D-F3-X-document-conversation-count-cap.
 */

// API source: packages/api/src/persona_api/services/image_service.py:86
// (MAX_UPLOAD_BYTES = 20 * 1024 * 1024)
export const MAX_UPLOAD_SIZE_BYTES: number = 20 * 1024 * 1024;

// API source: packages/api/src/persona_api/schemas/requests.py:143
// (PostMessageRequest.images Field(min_length=1, max_length=4))
export const MAX_IMAGES_PER_MESSAGE: number = 4;

// API source: packages/api/src/persona_api/schemas/requests.py:116
// (ImageRef.media_type Literal["image/png", "image/jpeg", "image/webp", "image/gif"])
export const IMAGE_MEDIA_TYPES = [
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
] as const;
export type ImageMediaType = (typeof IMAGE_MEDIA_TYPES)[number];

// API source: packages/core/src/persona/documents/parsers/__init__.py:121-164
// (SUPPORTED_EXTENSIONS = frozenset(_DISPATCH_TABLE.keys()))
// Stdlib-only first, then code extensions, then extra-gated (pdf/docx/xlsx).
export const DOCUMENT_EXTENSIONS = [
  ".txt",
  ".md",
  ".csv",
  ".py",
  ".js",
  ".jsx",
  ".ts",
  ".tsx",
  ".rs",
  ".go",
  ".java",
  ".kt",
  ".swift",
  ".rb",
  ".php",
  ".c",
  ".cc",
  ".cpp",
  ".h",
  ".hpp",
  ".cs",
  ".scala",
  ".sh",
  ".bash",
  ".zsh",
  ".sql",
  ".html",
  ".css",
  ".scss",
  ".yaml",
  ".yml",
  ".toml",
  ".json",
  ".xml",
  ".pdf",
  ".docx",
  ".xlsx",
] as const;
export type DocumentExtension = (typeof DOCUMENT_EXTENSIONS)[number];

// API source: NONE at v0.1 — Spec 14 ships no per-conversation document
// count cap (verified by grep over packages/api/src/persona_api/services/
// document_service.py for `MAX_DOCUMENTS` / `len(refs)` / `too_many`).
// Typed `number | null` so v0.2 can swap the value without TS surgery.
// v0.2 candidate: GET /v1/limits endpoint or per-conversation field.
export const MAX_DOCUMENTS_PER_CONVERSATION: number | null = null;

/**
 * Whether the declared MIME type is one of the four image types Spec 13's
 * upload service accepts. Used by the attach control's content-type
 * dispatch — anything `isImageMediaType` is true for routes to the
 * image branch; documents go through `isDocumentFilename`.
 *
 * Pure; safe to call in render. Server stays authoritative (Spec 13 runs
 * the magic-byte + decompression-bomb checks before persisting).
 */
export function isImageMediaType(
  mediaType: string,
): mediaType is ImageMediaType {
  return (IMAGE_MEDIA_TYPES as readonly string[]).includes(mediaType);
}

/**
 * Whether the filename's extension is in Spec 14's SUPPORTED_EXTENSIONS.
 * Falls back on the extension when `Content-Type` is missing or
 * ambiguous (common from non-browser drag sources). Match logic mirrors
 * `_is_document_filename` in routes/uploads.py.
 *
 * Returns `false` for empty/extensionless filenames; the composer's
 * pre-validation surfaces this as the F2 error voice rather than routing
 * the file to the wrong service.
 */
export function isDocumentFilename(filename: string): boolean {
  const lastDot = filename.lastIndexOf(".");
  if (lastDot === -1 || lastDot === filename.length - 1) return false;
  const ext = filename.slice(lastDot).toLowerCase();
  return (DOCUMENT_EXTENSIONS as readonly string[]).includes(ext);
}
