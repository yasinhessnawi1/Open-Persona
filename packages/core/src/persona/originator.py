"""The trigger-agnostic origination capability (Spec C0, T3, D-C0-1).

:class:`Originator` is the single callable the runtime invokes to signal "this
persona has something to say to this user". It is deliberately **trigger-agnostic**
— it knows nothing about *why* it was called. The within-runtime caller (T7 wires
the agentic run's conclusion to it) and, later, direction 4's autonomous trigger
drive the *same* :meth:`Originator.originate` → the same recording → the same
delivery, without modifying the primitive or any channel. That separation of the
*capability* from any *trigger* is criterion 8 (the whole reason origination is
built in the base), and it holds *by construction*: this module lives in
persona-core, which sits below persona-runtime and cannot import it.

Composition is constructor-injected (DI; no global state): a
:class:`OriginatedMessageRecorder` (the RLS-scoped conversation + episodic write
seam — filled in T4, where ownership is enforced) and a
:class:`~persona.delivery.MessageDeliverer` (the delivery boundary — the routing
facade in T6, a fake in tests). The capability itself carries the owner only;
the cross-tenant guard (:class:`~persona.errors.OriginationForbiddenError`) lives
where the write meets RLS (T4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from persona.schema.origination import OriginatedMessage

if TYPE_CHECKING:
    from datetime import datetime

    from persona.delivery import DeliveryResult, MessageDeliverer
    from persona.schema.origination import PersonaIdentityTag

__all__ = [
    "OriginatedMessageRecorder",
    "Originator",
]


@runtime_checkable
class OriginatedMessageRecorder(Protocol):
    """The seam that persists an originated message as a first-class citizen.

    Filled by persona-api in T4: write the originated message into the persona's
    conversation (starting one if ``message.conversation_id`` is ``None`` —
    D-C0-3) AND into episodic memory (the same path a reply uses — criterion 2),
    RLS-scoped to the owner. Raises
    :class:`~persona.errors.OriginationForbiddenError` on a cross-tenant target
    (criterion 9). ``@runtime_checkable`` so a composition root can assert the
    injected collaborator satisfies the seam.
    """

    async def record(self, message: OriginatedMessage) -> str:
        """Persist ``message`` and return its resolved conversation id.

        The return is the write receipt (the conversation the message landed in,
        whether pre-existing or freshly started) — used to address delivery; it
        is not a query result (CQS: the write does not otherwise return data).
        """
        ...


class Originator:
    """The origination capability — build → record → deliver → report (D-C0-1).

    Trigger-agnostic: any caller (the within-runtime conclusion, a future
    autonomous trigger) drives :meth:`originate` identically. Holds no state
    beyond its injected collaborators.
    """

    def __init__(
        self,
        recorder: OriginatedMessageRecorder,
        deliverer: MessageDeliverer,
    ) -> None:
        self._recorder = recorder
        self._deliverer = deliverer

    async def originate(
        self,
        *,
        persona: PersonaIdentityTag,
        owner_user_id: str,
        content: str,
        created_at: datetime,
        conversation_id: str | None = None,
    ) -> DeliveryResult:
        """Originate a message from ``persona`` to its owner and report delivery.

        Builds the :class:`OriginatedMessage`, records it (the conversation +
        episodic write seam; starts a conversation when ``conversation_id`` is
        ``None`` — D-C0-3), then hands it to the delivery boundary and returns the
        :class:`~persona.delivery.DeliveryResult`. ``owner_user_id`` is the only
        valid recipient (criterion 9); the cross-tenant guard fires in the
        recorder where the write meets RLS (T4) — this capability carries the
        owner, it does not itself enforce.
        """
        message = OriginatedMessage(
            persona=persona,
            owner_user_id=owner_user_id,
            content=content,
            conversation_id=conversation_id,
            created_at=created_at,
        )
        resolved_conversation_id = await self._recorder.record(message)
        if message.conversation_id is None:
            message = message.model_copy(update={"conversation_id": resolved_conversation_id})
        return await self._deliverer.deliver(message)
