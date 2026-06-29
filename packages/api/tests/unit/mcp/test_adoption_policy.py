"""Spec N4 B2-② — the vetted-set adoption policy (N4-D-6).

The edition-aware gate for catalog-discovered self-extension:
- v1-adoptable = a ``type: remote`` entry WITH a ``remote_url`` (N4-D-2 / N4-D-10);
  local-container entries are never adoptable;
- **community** = any such remote entry (the user owns the trust choice);
- **cloud** = only the operator allowlist (``PERSONA_MCP_ADOPT_VETTED``); the **empty
  default is deny-all (fail-closed)**.
Enforced at the grant boundary (the adopt route, B2-③) and mirrored at the ``mcp_search``
boundary (``vetted_catalog_for_search``).
"""

from __future__ import annotations

from persona.tools.mcp.catalog import MCPCatalog, MCPServerCatalogEntry
from persona_api.config import Edition
from persona_api.mcp.adoption_policy import (
    adoptable_catalog_names,
    is_adoptable,
    vetted_catalog_for_search,
)


def _entry(name: str, server_type: str, remote_url: str = "") -> MCPServerCatalogEntry:
    return MCPServerCatalogEntry(
        name=name,
        description="",
        kind="external",
        risk="low",
        server_type=server_type,  # type: ignore[arg-type]
        remote_url=remote_url,
    )


def _catalog(*entries: MCPServerCatalogEntry) -> MCPCatalog:
    return MCPCatalog(servers={e.name: e for e in entries})


_REMOTE_A = _entry("notion-remote", "remote", "https://mcp.notion.com/mcp")
_REMOTE_B = _entry("linear", "remote", "https://mcp.linear.app/mcp")
_LOCAL = _entry("github", "server")  # local-container, no remote endpoint
_REMOTE_NOURL = _entry("weird", "remote", "")  # remote but no url → not adoptable


class TestPolicy:
    def test_community_surfaces_all_remote_with_url(self) -> None:
        cat = _catalog(_REMOTE_A, _REMOTE_B, _LOCAL, _REMOTE_NOURL)
        names = adoptable_catalog_names(edition=Edition.community, vetted=[], catalog=cat)
        assert names == frozenset({"notion-remote", "linear"})

    def test_cloud_empty_allowlist_is_deny_all(self) -> None:
        cat = _catalog(_REMOTE_A, _REMOTE_B, _LOCAL)
        assert adoptable_catalog_names(edition=Edition.cloud, vetted=[], catalog=cat) == frozenset()

    def test_cloud_allowlist_is_the_subset_of_remote_vetted(self) -> None:
        cat = _catalog(_REMOTE_A, _REMOTE_B, _LOCAL)
        # ``github`` is allowlisted but local-container → still excluded (must be remote).
        names = adoptable_catalog_names(
            edition=Edition.cloud, vetted=["notion-remote", "github"], catalog=cat
        )
        assert names == frozenset({"notion-remote"})

    def test_local_container_never_adoptable_even_if_allowlisted(self) -> None:
        cat = _catalog(_LOCAL)
        assert not is_adoptable("github", edition=Edition.community, vetted=["github"], catalog=cat)
        assert not is_adoptable("github", edition=Edition.cloud, vetted=["github"], catalog=cat)

    def test_remote_without_url_never_adoptable(self) -> None:
        cat = _catalog(_REMOTE_NOURL)
        assert not is_adoptable("weird", edition=Edition.community, vetted=["weird"], catalog=cat)

    def test_is_adoptable_true_for_a_vetted_remote(self) -> None:
        cat = _catalog(_REMOTE_A)
        assert is_adoptable(
            "notion-remote", edition=Edition.cloud, vetted=["notion-remote"], catalog=cat
        )


class TestSearchBoundaryMirror:
    def test_search_catalog_cloud_empty_surfaces_nothing(self) -> None:
        cat = _catalog(_REMOTE_A, _LOCAL)
        filtered = vetted_catalog_for_search(edition=Edition.cloud, vetted=[], catalog=cat)
        assert filtered.servers == {}

    def test_search_catalog_community_surfaces_remote_only(self) -> None:
        cat = _catalog(_REMOTE_A, _LOCAL)
        filtered = vetted_catalog_for_search(edition=Edition.community, vetted=[], catalog=cat)
        assert set(filtered.servers) == {"notion-remote"}

    def test_search_catalog_cloud_surfaces_only_vetted_remote(self) -> None:
        cat = _catalog(_REMOTE_A, _REMOTE_B, _LOCAL)
        filtered = vetted_catalog_for_search(edition=Edition.cloud, vetted=["linear"], catalog=cat)
        assert set(filtered.servers) == {"linear"}


class TestScopedToCatalogAdoptionOnly:
    """N4-D-6 guard: the vetted gate scopes ONLY catalog-discovered adoption. The existing
    built-in / Spec-27 / N3 grant path (``grant_tool_consent``, N4-D-5) must NOT consult it —
    those servers are operator-vetted by virtue of shipping, so they stay grantable in cloud
    even under an empty (deny-all) allowlist. Proven structurally (the seam-separation
    pattern), like N1's reserved-seam marker."""

    def test_existing_grant_path_does_not_consult_the_vetted_gate(self) -> None:
        import inspect

        from persona_api.services import tool_consent_service

        src = inspect.getsource(tool_consent_service)
        assert "adoption_policy" not in src
        assert "vetted" not in src
        assert "mcp_adopt" not in src
        assert "is_adoptable" not in src

    def test_consent_target_validation_is_edition_and_vetted_blind(self) -> None:
        # The grant path validates a target against the known catalog (known_mcp_server_names),
        # never the vetted allowlist — so a shipped/built-in mcp server is grantable in cloud
        # with an empty PERSONA_MCP_ADOPT_VETTED. The validator takes no edition/vetted args.
        import inspect

        from persona_api.services.tool_consent_service import _is_valid_consent_target

        params = set(inspect.signature(_is_valid_consent_target).parameters)
        assert params == {"tool_name"}  # no edition, no vetted-list — structurally unscoped
