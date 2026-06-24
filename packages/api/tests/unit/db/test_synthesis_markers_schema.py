"""``synthesis_markers`` schema + migration shape (Spec K2, T8). Pure in-memory; no DB.

Locks the signed-off DDL (D-K2-X-migration-placeholder): the columns, the
(owner, kind, interaction) compare-and-set unique constraint, the kind CHECK, the
owner FK, and the placeholder migration chaining. Also asserts the table is
cloud-only (excluded from the community SQLite build, like the graph tables).
"""

from __future__ import annotations

from persona_api.db.community import build_community_metadata
from persona_api.db.models import synthesis_markers


def test_columns() -> None:
    cols = set(synthesis_markers.columns.keys())
    assert cols == {
        "id",
        "owner_id",
        "interaction_kind",
        "interaction_id",
        "synthesised_up_to",
        "synthesised_at",
        "created_at",
    }


def test_owner_fk_cascades_to_users() -> None:
    fks = list(synthesis_markers.c.owner_id.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "users"
    assert fks[0].ondelete == "CASCADE"


def test_compare_and_set_unique_and_kind_check_present() -> None:
    names = {c.name for c in synthesis_markers.constraints}
    assert "uq_synthesis_markers_owner_kind_interaction" in names
    assert "synthesis_markers_kind_check" in names


def test_synthesised_up_to_defaults_to_zero() -> None:
    default = synthesis_markers.c.synthesised_up_to.server_default
    assert default is not None
    assert "0" in str(default.arg)


def test_migration_uses_the_placeholder_down_revision() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "alembic" / "versions" / "015_synthesis_markers.py"
    spec = importlib.util.spec_from_file_location("_k2_synthesis_markers_migration", path)
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "015_synthesis_markers"
    # Linearized at merge-back behind A1's 014_schedules.
    assert migration.down_revision == "014_schedules"


def test_table_is_cloud_only_excluded_from_community_build() -> None:
    community = build_community_metadata()
    assert "synthesis_markers" not in community.tables
