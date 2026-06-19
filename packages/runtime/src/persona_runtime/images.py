"""Per-turn uploaded-image value type for the image-workspace cascade.

A :class:`TurnImage` is the runtime-layer, transport-agnostic carrier for one
uploaded image on the current turn. It bundles the two things the
:class:`~persona_runtime.loop.ConversationLoop` needs to route an image to its
two destinations:

* the **model** — ``workspace_path`` + ``media_type`` become an
  :class:`persona.schema.content.ImageContent` block on the multimodal user
  message (the backend serialisers resolve the workspace path to bytes at send
  time, Spec 13 T05/T06);
* the **sandbox** — ``content_bytes`` is staged as a
  :class:`persona.sandbox.result.SandboxFile` in the loop's
  ``deferred_input_files`` so ``code_execution`` can read the image as a file.

The caller (the hosted ``chat_service`` or any other path) resolves the bytes
from wherever the upload lives (e.g. the ``/uploads/{ref}`` storage) and hands
the loop a ``TurnImage`` — the loop never touches transport-specific storage.
This keeps Parts 1–3 of the cascade path-agnostic per the bug-fix scope.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["TurnImage"]

#: The four supported image MIME types (mirrors
#: :class:`persona.schema.content.ImageContent` / Spec 13 D-13-3).
ImageMediaType = Literal["image/png", "image/jpeg", "image/webp", "image/gif"]


class TurnImage(BaseModel):
    """One uploaded image attached to the current user turn.

    Attributes:
        workspace_path: Workspace-relative reference (``uploads/<ref>.<ext>``)
            used to build the :class:`ImageContent` block for the model. The
            backend serialiser resolves it to bytes against its configured
            ``workspace_root`` at send time.
        media_type: One of the four supported image MIME types.
        content_bytes: The raw image bytes, used to stage the image as a
            sandbox input file. Resolved by the caller from the upload store;
            never read from disk by the loop.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_path: str = Field(min_length=1)
    media_type: ImageMediaType
    content_bytes: bytes
