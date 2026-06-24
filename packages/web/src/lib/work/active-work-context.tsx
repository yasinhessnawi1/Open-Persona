"use client";

/**
 * Spec P1 (D-P1-v7-indicator) — the app-level "active work" session.
 *
 * The sibling of V7's `CallSessionProvider` for CHAT turns: it tracks which
 * conversations have an in-progress detached turn so a subtle "working"
 * indicator can show on the conversation row (and a global return-to-it bar),
 * advertising the very work the reattach surfaces (T4/T7) let you rejoin.
 *
 * Mechanics are per-surface (we do NOT touch `CallSession`): the chat hook
 * registers a conversation when its turn starts and unregisters when the turn
 * ends WHILE MOUNTED. A navigate-away unmounts the hook (the turn keeps running
 * server-side), so the hook can't observe completion-while-away — this provider
 * therefore POLLS each registered conversation's `…/active-turn` and clears the
 * indicator when the turn finishes (404). That keeps the global indicators
 * honest without coupling to the open page. Runs are already covered by the
 * pulsing `RunStatusBadge` + the row link, so this layer is chat-only.
 *
 * A no-op DEFAULT lets `useChat` call `useActiveWork()` outside the provider
 * (unit tests) without a crash.
 */

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useAuth } from "@/auth";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TEMPLATE = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;
const POLL_MS = 4000;

export interface ActiveChatWork {
  conversationId: string;
  personaId: string;
}

interface ActiveWorkValue {
  /** Conversations with an in-progress detached chat turn. */
  readonly activeChats: ActiveChatWork[];
  isChatActive: (conversationId: string) => boolean;
  registerChat: (work: ActiveChatWork) => void;
  unregisterChat: (conversationId: string) => void;
}

const DEFAULT: ActiveWorkValue = {
  activeChats: [],
  isChatActive: () => false,
  registerChat: () => {},
  unregisterChat: () => {},
};

const ActiveWorkContext = createContext<ActiveWorkValue>(DEFAULT);

export function useActiveWork(): ActiveWorkValue {
  return useContext(ActiveWorkContext);
}

export function ActiveWorkProvider({ children }: { children: ReactNode }) {
  const { getToken } = useAuth();
  const [chats, setChats] = useState<Record<string, ActiveChatWork>>({});

  const registerChat = useCallback((work: ActiveChatWork) => {
    setChats((m) =>
      m[work.conversationId] ? m : { ...m, [work.conversationId]: work },
    );
  }, []);

  const unregisterChat = useCallback((conversationId: string) => {
    setChats((m) => {
      if (!m[conversationId]) return m;
      const { [conversationId]: _omit, ...rest } = m;
      return rest;
    });
  }, []);

  // Poll registered conversations so an indicator clears when its turn finishes
  // WHILE the user is away (the chat hook is unmounted then and can't observe it).
  const chatsRef = useRef(chats);
  chatsRef.current = chats;
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const ids = Object.keys(chatsRef.current);
      if (ids.length === 0) return;
      let jwt: string | null | undefined;
      try {
        jwt = await getToken(TEMPLATE ? { template: TEMPLATE } : undefined);
      } catch {
        return;
      }
      await Promise.all(
        ids.map(async (id) => {
          try {
            const res = await fetch(
              `${API}/v1/conversations/${id}/active-turn`,
              {
                headers: { Authorization: `Bearer ${jwt}` },
              },
            );
            // 404 ⇒ no live turn (finished / interrupted) → clear the indicator.
            if (!cancelled && res.status === 404) unregisterChat(id);
          } catch {
            // best-effort; a transient failure just leaves the indicator until next tick
          }
        }),
      );
    };
    const handle = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, [getToken, unregisterChat]);

  const value = useMemo<ActiveWorkValue>(() => {
    const activeChats = Object.values(chats);
    const ids = new Set(activeChats.map((c) => c.conversationId));
    return {
      activeChats,
      isChatActive: (conversationId) => ids.has(conversationId),
      registerChat,
      unregisterChat,
    };
  }, [chats, registerChat, unregisterChat]);

  return (
    <ActiveWorkContext.Provider value={value}>
      {children}
    </ActiveWorkContext.Provider>
  );
}
