"""Row-level-security policy SQL (spec 07, T06, D-07-5).

Every tenant-scoped table gets RLS ``ENABLE`` + ``FORCE`` (so even the table
owner is subject to policy — without ``FORCE`` a privileged connection bypasses
RLS and the isolation test would pass while production leaks) and a policy that
restricts visibility to the current request's user.

The request's user id is read from the ``app.current_user_id`` session GUC,
which the API sets per-request via ``set_config('app.current_user_id', :uid,
true)`` inside a transaction (NOT ``SET LOCAL ... = :uid``, which is a syntax
error with a bound parameter — see ``engine.py`` and research §6). The
missing-ok ``current_setting('app.current_user_id', true)`` form fails CLOSED:
an unset GUC yields NULL, which matches no row.

Each table's policy uses the correct FK-chain join (the joins genuinely differ —
``messages`` has no ``owner_id``; ``memory_chunks`` joins through ``personas``):

- personas / conversations / runs: direct ``owner_id``
- messages / turn_logs:           through ``conversations.owner_id``
- memory_chunks:                  through ``personas.owner_id``
- credits / credit_transactions:  direct ``user_id``

``audit_log`` and ``rate_limit_buckets`` are deliberately NOT under RLS: the
audit log is append-only platform forensics (read by admins, not tenant-scoped),
and rate-limit buckets are keyed by ``user_id`` but accessed by the platform's
own limiter, not per-tenant queries. Spec 08 confirms their access patterns.
"""

from __future__ import annotations

__all__ = ["RLS_TABLES", "downgrade_rls_sql", "upgrade_rls_sql"]

_CUR = "current_setting('app.current_user_id', true)"

# table -> the USING predicate restricting rows to the current user.
_POLICIES: dict[str, str] = {
    "personas": f"owner_id = {_CUR}",
    "conversations": f"owner_id = {_CUR}",
    "runs": f"owner_id = {_CUR}",
    "messages": (f"conversation_id IN (SELECT id FROM conversations WHERE owner_id = {_CUR})"),
    "turn_logs": (f"conversation_id IN (SELECT id FROM conversations WHERE owner_id = {_CUR})"),
    "memory_chunks": (f"persona_id IN (SELECT id FROM personas WHERE owner_id = {_CUR})"),
    "credits": f"user_id = {_CUR}",
    "credit_transactions": f"user_id = {_CUR}",
}

# Spec 14 + F3 follow-up — auxiliary RLS policies for the DocumentStore path.
# CSA-1 calling-convention discipline: DocumentStore calls
# ``MemoryStore.write(persona_id=<conversation_id>, ...)`` which fails the
# default ``memory_chunks`` policy (the persona_id isn't in personas). The
# auxiliary policy permits the document path explicitly: rows with
# ``kind = 'document'`` whose persona_id is a conversation owned by the
# current user. Both policies are PERMISSIVE → Postgres ORs them. The
# ``kind = 'document'`` gate keeps the policies non-overlapping: a typed-
# store write (kind ∈ identity/self_facts/worldview/episodic) can NEVER
# satisfy the document policy AND a document write can NEVER satisfy the
# persona policy (DocumentStore never writes with the persona's real id
# in the persona_id slot). Migration 005 lands this for existing DBs;
# upgrade_rls_sql + downgrade_rls_sql emit it for fresh installs.
_AUX_POLICIES: dict[str, dict[str, str]] = {
    "memory_chunks": {
        "user_isolation_documents": (
            f"kind = 'document' "
            f"AND persona_id IN (SELECT id FROM conversations WHERE owner_id = {_CUR})"
        ),
    },
}

RLS_TABLES = tuple(_POLICIES.keys())


def upgrade_rls_sql() -> list[str]:
    """SQL statements to enable + force RLS and create per-table policies.

    The policy is applied to both reads (``USING``) and writes
    (``WITH CHECK``) so a tenant can neither see nor insert/update rows
    outside their scope.
    """
    statements: list[str] = []
    for table, predicate in _POLICIES.items():
        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        statements.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        statements.append(
            f"CREATE POLICY user_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate})"
        )
    # Auxiliary policies (Spec 14 DocumentStore). Permissive → OR-combined.
    for table, policies in _AUX_POLICIES.items():
        for name, predicate in policies.items():
            statements.append(
                f"CREATE POLICY {name} ON {table} USING ({predicate}) WITH CHECK ({predicate})"
            )
    return statements


def downgrade_rls_sql() -> list[str]:
    """SQL statements to drop the policies and disable RLS (reverse order)."""
    statements: list[str] = []
    # Auxiliary policies drop first (no FK between policies but mirror upgrade order).
    for table, policies in _AUX_POLICIES.items():
        for name in policies:
            statements.append(f"DROP POLICY IF EXISTS {name} ON {table}")
    for table in _POLICIES:
        statements.append(f"DROP POLICY IF EXISTS user_isolation ON {table}")
        statements.append(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        statements.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    return statements
