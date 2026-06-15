"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

/** The artifact currently shown in the right-panel renderer. */
export interface OpenArtifact {
  workspacePath: string;
  mediaType: string;
  name: string;
}

interface FileRendererContextValue {
  current: OpenArtifact | null;
  open: (artifact: OpenArtifact) => void;
  close: () => void;
}

const FileRendererContext = createContext<FileRendererContextValue | null>(
  null,
);

/**
 * Spec 28 — conversation-scoped right-panel state (D-28-6). Mounted once per
 * conversation; the panel persists across messages and closes when the
 * conversation changes (a fresh provider mounts → `current` resets to null).
 */
export function FileRendererProvider({ children }: { children: ReactNode }) {
  const [current, setCurrent] = useState<OpenArtifact | null>(null);
  const open = useCallback(
    (artifact: OpenArtifact) => setCurrent(artifact),
    [],
  );
  const close = useCallback(() => setCurrent(null), []);
  const value = useMemo(
    () => ({ current, open, close }),
    [current, open, close],
  );
  return (
    <FileRendererContext.Provider value={value}>
      {children}
    </FileRendererContext.Provider>
  );
}

/** Access the right-panel renderer controls. Returns a no-op fallback when no
 *  provider is mounted (e.g. unit tests rendering a FileCard in isolation). */
export function useFileRenderer(): FileRendererContextValue {
  const ctx = useContext(FileRendererContext);
  if (ctx === null) {
    return { current: null, open: () => undefined, close: () => undefined };
  }
  return ctx;
}
