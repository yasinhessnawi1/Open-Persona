import { AppShell } from "@/components/shell/app-shell";

// F2 T19: the layout delegates to <AppShell> which owns the sidebar +
// header + main composition + the F1 token consumption (--elevation-1 +
// retokenised motion). Persona context is set by per-route <PersonaProvider>
// wrappers (chat/run/detail) rather than at the layout level — only routes
// that know their persona advertise it.
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
