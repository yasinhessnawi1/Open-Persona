"use client";

import { ChevronDown } from "lucide-react";
import { useTranslations } from "next-intl";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * Collapsible section cards + a Settings-style left timeline nav for the persona
 * editor. A `<SectionGroup>` provides shared open-state + an ordered registry; a
 * `<CollapsibleSection>` registers itself (so the nav lists every section,
 * including any a sibling spec adds) and reads/writes its open state from the
 * group. Used WITHOUT a group it falls back to local state, so the component is
 * reusable + testable standalone.
 *
 * Default-open is set per section: the editor opens the first (Identity) card and
 * leaves the rest collapsed; the authoring flow additionally opens the
 * clarifying-questions card. The user expands/collapses any card via its header.
 */

interface SectionEntry {
  id: string;
  title: string;
}

interface SectionGroupValue {
  entries: SectionEntry[];
  open: Record<string, boolean>;
  register: (id: string, title: string, defaultOpen: boolean) => void;
  unregister: (id: string) => void;
  setOpen: (id: string, value: boolean) => void;
}

const SectionGroupContext = createContext<SectionGroupValue | null>(null);

export function SectionGroup({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<SectionEntry[]>([]);
  const [open, setOpenState] = useState<Record<string, boolean>>({});

  const register = useCallback(
    (id: string, title: string, defaultOpen: boolean) => {
      setEntries((prev) =>
        prev.some((e) => e.id === id) ? prev : [...prev, { id, title }],
      );
      setOpenState((prev) =>
        id in prev ? prev : { ...prev, [id]: defaultOpen },
      );
    },
    [],
  );

  const unregister = useCallback((id: string) => {
    setEntries((prev) => prev.filter((e) => e.id !== id));
  }, []);

  const setOpen = useCallback((id: string, value: boolean) => {
    setOpenState((prev) => ({ ...prev, [id]: value }));
  }, []);

  const value = useMemo<SectionGroupValue>(
    () => ({ entries, open, register, unregister, setOpen }),
    [entries, open, register, unregister, setOpen],
  );

  return (
    <SectionGroupContext.Provider value={value}>
      {children}
    </SectionGroupContext.Provider>
  );
}

/**
 * Collapsible state for one section. Integrates with the surrounding
 * `<SectionGroup>` when present (so the nav can list + expand it); otherwise
 * keeps local state.
 */
function useCollapsible(id: string, title: string, defaultOpen: boolean) {
  const group = useContext(SectionGroupContext);
  const [localOpen, setLocalOpen] = useState(defaultOpen);
  // defaultOpen is the initial value only; held in a ref so re-registering never
  // fights a user toggle (and isn't a reactive effect dependency).
  const defaultOpenRef = useRef(defaultOpen);

  const { register, unregister } = group ?? {};
  useEffect(() => {
    if (!register || !unregister) return;
    register(id, title, defaultOpenRef.current);
    return () => unregister(id);
  }, [id, title, register, unregister]);

  const open = group ? (group.open[id] ?? defaultOpen) : localOpen;
  const setOpen = group
    ? (value: boolean) => group.setOpen(id, value)
    : setLocalOpen;
  return { open, setOpen };
}

export function CollapsibleSection({
  id,
  title,
  defaultOpen = false,
  headerAccessory,
  children,
}: {
  id: string;
  title: string;
  defaultOpen?: boolean;
  /** Optional control rendered in the header, left of the chevron (e.g. a toggle). */
  headerAccessory?: ReactNode;
  children: ReactNode;
}) {
  const t = useTranslations("author");
  const { open, setOpen } = useCollapsible(id, title, defaultOpen);
  const bodyId = useId();

  return (
    <Card
      id={id}
      className="scroll-mt-20 gap-0 p-0"
      data-slot="collapsible-section"
      data-open={open}
    >
      <div className="flex items-center gap-3 p-5">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          aria-controls={bodyId}
          className="-m-1 flex flex-1 items-center justify-between gap-3 rounded p-1 text-left"
          data-slot="collapsible-trigger"
        >
          <h2 className="font-heading text-sm font-semibold tracking-wide text-muted-foreground uppercase">
            {title}
          </h2>
          <ChevronDown
            className={cn(
              "size-4 shrink-0 text-muted-foreground transition-transform duration-200 motion-reduce:transition-none",
              open && "rotate-180",
            )}
            aria-hidden
          />
        </button>
        {headerAccessory ? (
          <div className="shrink-0">{headerAccessory}</div>
        ) : null}
      </div>
      <section
        id={bodyId}
        aria-label={title}
        className={cn(
          "grid transition-[grid-template-rows] duration-200 motion-reduce:transition-none",
          open ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        )}
      >
        <div className="overflow-hidden">
          <div className="flex flex-col gap-3 px-5 pb-5" inert={!open}>
            {children}
          </div>
        </div>
      </section>
      <span className="sr-only">{t(open ? "collapse" : "expand")}</span>
    </Card>
  );
}

/**
 * The Settings-style left timeline nav. Reads the group's ordered registry, so
 * it always reflects the sections actually rendered. Clicking a link opens the
 * target section (if collapsed) and scrolls to it.
 */
export function SectionTimelineNav({ className }: { className?: string }) {
  const t = useTranslations("author");
  const group = useContext(SectionGroupContext);
  if (!group || group.entries.length === 0) return null;

  return (
    <nav
      aria-label={t("sectionsNav")}
      className={cn("sticky top-20 hidden self-start lg:block", className)}
      data-slot="section-timeline-nav"
    >
      <ul className="flex flex-col gap-1 border-l text-muted-foreground">
        {group.entries.map((e) => (
          <li key={e.id}>
            <a
              href={`#${e.id}`}
              onClick={(ev) => {
                ev.preventDefault();
                group.setOpen(e.id, true);
                requestAnimationFrame(() => {
                  document
                    .getElementById(e.id)
                    ?.scrollIntoView({ behavior: "smooth", block: "start" });
                });
              }}
              className="type-ui block border-l-2 border-transparent px-3 py-1 hover:border-primary hover:text-foreground"
              data-active={group.open[e.id] ? "true" : "false"}
            >
              {e.title}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
