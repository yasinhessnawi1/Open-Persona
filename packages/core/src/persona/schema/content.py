"""Typed content blocks for multimodal ``ConversationMessage`` content.

This is the Spec 13 typed surface that lets a single ``ConversationMessage``
carry interleaved text and image references. Per **D-13-X-now option (c)**
(locked in ``docs/specs/phase2/spec_13/decisions.md``), the persisted image
reference is a workspace path string — image bytes live exactly once under
the persona's Spec 03 workspace and the message store only ever holds the
reference. This keeps the headline Dominant Concern #2 invariant true:
the messages table is bounded by reference count, not image bytes.

T03 picks how :class:`MessageContent` is wired onto
``ConversationMessage.content`` (str-or-list widening + a model_validator).
This module owns the *content blocks themselves* — ``TextContent`` and
``ImageContent`` — plus the tagged-union alias :data:`MessageContent`. The
``type`` field on each block is the Pydantic discriminator key so the
union can be resolved structurally when serialised to/from JSON.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ImageContent", "MessageContent", "TextContent"]


class TextContent(BaseModel):
    """A text block within a multimodal message ``content`` list.

    Attributes:
        type: Discriminator tag — always the literal ``"text"`` so the
            :data:`MessageContent` tagged union can resolve this block by
            its ``type`` field on deserialisation.
        text: The text payload itself.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["text"] = "text"
    text: str


class ImageContent(BaseModel):
    """An image *reference* block within a multimodal message ``content`` list.

    Per Spec 13 D-13-X-now option (c), the message store carries only the
    workspace reference — image bytes live exactly once under the persona's
    Spec 03 workspace and are resolved at send time by the backend
    serialisers (Spec 13 T05/T06). This is the structural guard behind
    Dominant Concern #2: the ``messages`` table size grows with reference
    count, not with image bytes. See
    ``docs/specs/phase2/spec_13/decisions.md`` (D-13-X-now) and the T13
    store-by-reference regression test.

    Attributes:
        type: Discriminator tag — always the literal ``"image"`` so the
            :data:`MessageContent` tagged union can resolve this block by
            its ``type`` field on deserialisation.
        workspace_path: The reference into the persona workspace (Spec 03).
            Resolved to bytes only at backend-send time; the message store
            never holds the bytes themselves.
        media_type: One of the four supported image MIME types per
            **D-13-3**: ``image/png``, ``image/jpeg``, ``image/webp``,
            ``image/gif``. Any other value is rejected at validation time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["image"] = "image"
    workspace_path: str
    media_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"]


MessageContent = Annotated[TextContent | ImageContent, Field(discriminator="type")]
"""Discriminated union of message content blocks.

The ``type`` field on each member is the discriminator key, which lets
Pydantic resolve the concrete block class structurally when a multimodal
``ConversationMessage.content`` list is deserialised from JSON. T03 wires
this alias onto ``ConversationMessage.content`` (widening the existing
``str`` to ``str | list[MessageContent]``).
"""
