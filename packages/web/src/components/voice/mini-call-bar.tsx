"use client";

/**
 * Spec V7 D-V7-2 — the persistent mini call-bar.
 *
 * A global, compact call surface rendered in the shell whenever a call is active
 * (it BINDS the hoisted session via {@link useCallSession} — it never owns a
 * `Room`). It is the call's control surface from anywhere: persona chip, elapsed
 * timer, speaking indicator, mute, end, and "return to the full call view".
 *
 * Art-direction (user, eyes-on): a **floating pill anchored bottom-RIGHT** (not
 * centre — the chat composer lives there), **collapsible** to a compact avatar-
 * only puck, and **user-draggable** (a grip handle) so it can be moved off
 * anything it covers. Mobile is the same compact pill. Drag position is held in
 * component state and survives navigation (the bar is mounted once in the shell,
 * so it never unmounts mid-call).
 */

import { ChevronDown, GripVertical, Maximize2, PhoneOff } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { PersonaAvatar } from "@/components/persona/persona-avatar";
import { InputModeToggle, MicControl } from "@/components/voice/mic-control";
import { personaIdentityStyle } from "@/lib/persona-identity";
import { useCallSession } from "@/lib/voice/call-session-context";

const MARGIN = 8;

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

export function MiniCallBar(): React.JSX.Element | null {
  const t = useTranslations("voice");
  const pathname = usePathname();
  const { state, target, isActive, startedAt, end } = useCallSession();

  const [collapsed, setCollapsed] = useState(false);
  // `null` → anchored bottom-right (the default); once dragged, fixed left/top px.
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const pillRef = useRef<HTMLElement | null>(null);
  const dragRef = useRef<{ dx: number; dy: number } | null>(null);

  // Tick the elapsed timer once a second while a call is live.
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (!isActive) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [isActive]);

  if (!isActive || target === null) return null;
  // Projection (D-V7-2): on the call's own full-view route the surface IS the
  // call, so the mini-bar collapses away; everywhere else it is the call.
  if (pathname === `/chat/${target.conversationId}/voice`) return null;

  const elapsed = startedAt !== null ? formatElapsed(now - startedAt) : "0:00";

  const statusLabel =
    state.phase === "connecting"
      ? t("connecting")
      : state.phase === "reconnecting"
        ? t("reconnecting")
        : state.phase === "dropped"
          ? t("dropped")
          : state.agentState === "thinking"
            ? t("thinking")
            : state.agentState === "speaking"
              ? t("speaking")
              : t("listening");

  const speaking = state.agentState === "speaking";

  const onPointerDown = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const el = pillRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    dragRef.current = { dx: e.clientX - rect.left, dy: e.clientY - rect.top };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const grip = dragRef.current;
    const el = pillRef.current;
    if (!grip || !el) return;
    setPos({
      left: clamp(
        e.clientX - grip.dx,
        MARGIN,
        window.innerWidth - el.offsetWidth - MARGIN,
      ),
      top: clamp(
        e.clientY - grip.dy,
        MARGIN,
        window.innerHeight - el.offsetHeight - MARGIN,
      ),
    });
  };
  const onPointerUp = (e: ReactPointerEvent<HTMLButtonElement>) => {
    dragRef.current = null;
    e.currentTarget.releasePointerCapture(e.pointerId);
  };

  const persona = {
    id: target.personaId,
    name: target.personaName,
    avatarUrl: target.personaAvatarUrl ?? undefined,
    role: target.personaRole,
  };

  // Anchored bottom-right by default; absolute coords once the user drags it.
  const style: React.CSSProperties = {
    ...personaIdentityStyle({ id: target.personaId }),
    ...(pos
      ? { left: pos.left, top: pos.top }
      : { right: "1rem", bottom: "calc(1rem + env(safe-area-inset-bottom))" }),
  };

  const grip = (
    <button
      type="button"
      aria-label={t("mini.move")}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      className="flex cursor-grab touch-none items-center text-muted-foreground active:cursor-grabbing"
    >
      <GripVertical className="size-4" aria-hidden />
    </button>
  );

  const avatar = (
    <span
      className={`relative inline-flex rounded-full ring-2 ring-[var(--v-id)] ${
        speaking ? "animate-pulse" : ""
      }`}
    >
      <PersonaAvatar persona={persona} size="sm" />
    </span>
  );

  return (
    <section
      ref={pillRef}
      aria-label={t("mini.region", { name: target.personaName })}
      style={style}
      data-slot="mini-call-bar"
      className="v-id fixed z-40 flex items-center gap-2 rounded-full border bg-background/95 px-2 py-1.5 shadow-[var(--elevation-2)] backdrop-blur"
    >
      {grip}
      {collapsed ? (
        <button
          type="button"
          aria-label={t("mini.expand")}
          onClick={() => setCollapsed(false)}
          className="flex items-center"
        >
          {avatar}
        </button>
      ) : (
        <>
          {avatar}
          <div className="flex min-w-0 flex-col leading-tight">
            <span className="truncate font-medium text-sm">
              {target.personaName}
            </span>
            <span className="font-mono text-muted-foreground type-caption normal-case tracking-normal">
              {statusLabel} {elapsed}
            </span>
          </div>
          {/* D-V7-6: mute toggle, or a hold-to-talk button in push-to-talk. */}
          <MicControl className="v-iconbtn" />
          {/* D-V7-6: the listening-mode toggle (always-listening ↔ push-to-talk)
              — also here so a call started in PTT isn't a one-way trap from the
              mini-bar. */}
          <InputModeToggle className="v-iconbtn" />
          <Link
            href={`/chat/${target.conversationId}/voice`}
            aria-label={t("mini.returnToCall")}
            className="v-iconbtn"
          >
            <Maximize2 aria-hidden />
          </Link>
          <button
            type="button"
            onClick={() => void end()}
            aria-label={t("end")}
            className="v-iconbtn text-destructive"
          >
            <PhoneOff aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => setCollapsed(true)}
            aria-label={t("mini.collapse")}
            className="v-iconbtn"
          >
            <ChevronDown aria-hidden />
          </button>
        </>
      )}
    </section>
  );
}
