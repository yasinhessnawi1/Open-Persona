# persona-connectors

The **connector framework** (Spec C1) — the trunk that makes a persona reachable
on messaging platforms (Telegram, Discord, Slack, WhatsApp, SMS, email). All
per-platform adapters (C2–C5) plug into it.

**Product model:** *my persona, reachable by me* — an authenticated extension of
the user's own account onto a platform, not a public bot. Ownership/RLS are
unchanged from the web (Spec 08).

The framework owns everything shared across platforms:

- the inbound → route → respond → outbound flow;
- the `Connector` protocol + normalisation contracts (designed to fit all six
  platforms — the email/SMS floor, with real-time/threads/formatting as optional
  capabilities);
- the **per-persona parallel-conversation model** (each persona has ≤1 active
  conversation per user per channel; switching personas *suspends* — never ends —
  the previous one; only `/new` and the idle-timeout end a conversation);
- persona-selection / name-parsing;
- account-linking and identity-mapping (the security spine);
- the outbound path consuming C0 (identity-tagged delivery).

## Architecture

`persona-connectors` is a separate long-lived process (the 3rd, after
`persona-api` and `persona-voice`). Per **C1-D-1** it reuses `persona-api`'s
reply-producing chat flow + C0's delivery router **in-process**, following the
`run_worker.py` pattern (a separate process that imports api services and sets
the `current_user_id` RLS contextvar). License: **PolyForm-Noncommercial-1.0.0**
(the application layer), not the MIT engine license.

The **owned surface** (`persona_connectors.domain`) is import-decoupled from
`persona_api`; the api-coupling lives only in `persona_connectors.composition`,
so a future extract-to-core is a dependency swap, not a reshape.
