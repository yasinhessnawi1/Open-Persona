import type { components } from "@/lib/api/schema";
import type { McpCatalogEntry } from "./persona-form";

/**
 * Spec 30 T11 — map the API `MCPCatalogServer` rows (GET /v1/mcp-catalog) onto
 * the form's `McpCatalogEntry` shape (snake_case → camelCase). Shared by the
 * new + edit persona pages so the mapping has one home.
 */
export function mapMcpCatalog(
  rows: components["schemas"]["MCPCatalogServer"][],
): McpCatalogEntry[] {
  return rows.map((r) => ({
    name: r.name,
    description: r.description,
    provider: r.provider,
    defaultEnabled: r.default_enabled,
    requiredEnv: r.required_env ?? [],
  }));
}
