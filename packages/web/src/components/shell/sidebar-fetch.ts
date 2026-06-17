import "server-only";

import { serverApi } from "@/lib/api/server";
import {
  rankPersonasByRecency,
  resolveConversations,
  type SidebarData,
} from "./sidebar-data";

/** How many recent personas the rail surfaces + how many message rows to load. */
const RAIL_PERSONAS = 4;
const MESSAGE_ROWS = 30;

/**
 * Resolve the sidebar's PERSONAS rail + MESSAGES list from data the app already
 * exposes (`GET /v1/personas`, `GET /v1/conversations`). No new endpoint, no
 * recency schema — the same derivation the dashboard uses (`rankPersonasByRecency`).
 *
 * Fail-soft: the sidebar is chrome, never the page's reason for being. Any fetch
 * failure (a cold token, a transient API blip) degrades to empty sections rather
 * than throwing and taking down every authenticated route.
 */
export async function fetchSidebarData(): Promise<SidebarData> {
  try {
    const api = await serverApi();
    const [personasRes, conversationsRes] = await Promise.all([
      api.GET("/v1/personas"),
      api.GET("/v1/conversations", {
        params: { query: { limit: MESSAGE_ROWS, offset: 0 } },
      }),
    ]);
    const personas = personasRes.data ?? [];
    const conversations = conversationsRes.data ?? [];

    return {
      personas: rankPersonasByRecency(personas, conversations).slice(
        0,
        RAIL_PERSONAS,
      ),
      conversations: resolveConversations(conversations, personas),
    };
  } catch {
    return { personas: [], conversations: [] };
  }
}
