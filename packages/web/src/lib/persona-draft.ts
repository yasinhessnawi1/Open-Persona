import yaml from "js-yaml";

// Editable persona-document model for the authoring + edit flows (T08, D-09-9).
// The parsed object (`PersonaDoc`) is the single source of truth; the form edits
// known fields while preserving any unknown keys (routing, embedding, visibility,
// persona_id …) by spreading. Invalid YAML keeps the last valid doc (the editor
// surfaces the error). Mirrors the v1.0 schema in
// packages/core/src/persona/schema/persona.py.

export type PersonaDoc = Record<string, unknown>;

export const EPISTEMIC_OPTIONS = [
  "fact",
  "belief",
  "hypothesis",
  "contested",
] as const;

export interface IdentityView {
  name: string;
  role: string;
  background: string;
  language_default: string;
  constraints: string[];
}

export interface SelfFactView {
  fact: string;
  confidence: number;
}

export interface WorldviewView {
  claim: string;
  domain: string;
  epistemic: string;
  confidence: number;
  valid_time: string;
}

function asRecord(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : {};
}

function asString(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

function asNumber(v: unknown, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function asStringList(v: unknown): string[] {
  return Array.isArray(v)
    ? v.filter((x): x is string => typeof x === "string")
    : [];
}

/** A minimal valid-shaped v1.0 skeleton (fields are filled by the user/author). */
export function emptyPersonaDoc(): PersonaDoc {
  return {
    schema_version: "1.0",
    identity: {
      name: "",
      role: "",
      background: "",
      language_default: "en",
      constraints: [],
    },
    self_facts: [],
    worldview: [],
    tools: [],
    skills: [],
  };
}

/** Serialise a doc to YAML (block style, no wrapping of long background text). */
export function docToYaml(doc: PersonaDoc): string {
  return yaml.dump(doc, { lineWidth: -1, noRefs: true, sortKeys: false });
}

/** Parse YAML into a doc; throws on invalid YAML or a non-mapping top level. */
export function yamlToDoc(src: string): PersonaDoc {
  const parsed = yaml.load(src);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Persona YAML must be a mapping at the top level.");
  }
  return parsed as PersonaDoc;
}

// ----- typed readers -----

export function readIdentity(doc: PersonaDoc): IdentityView {
  const id = asRecord(doc.identity);
  return {
    name: asString(id.name),
    role: asString(id.role),
    background: asString(id.background),
    language_default: asString(id.language_default, "en"),
    constraints: asStringList(id.constraints),
  };
}

export function readSelfFacts(doc: PersonaDoc): SelfFactView[] {
  const list = Array.isArray(doc.self_facts) ? doc.self_facts : [];
  return list.map((f) => {
    const r = asRecord(f);
    return { fact: asString(r.fact), confidence: asNumber(r.confidence, 1) };
  });
}

export function readWorldview(doc: PersonaDoc): WorldviewView[] {
  const list = Array.isArray(doc.worldview) ? doc.worldview : [];
  return list.map((w) => {
    const r = asRecord(w);
    return {
      claim: asString(r.claim),
      domain: asString(r.domain),
      epistemic: asString(r.epistemic, "belief"),
      confidence: asNumber(r.confidence, 0.8),
      valid_time: asString(r.valid_time, "always"),
    };
  });
}

export function readStringList(doc: PersonaDoc, key: string): string[] {
  return asStringList(doc[key]);
}

// ----- immutable writers (return a new doc, preserving sibling keys) -----

export function writeIdentityField(
  doc: PersonaDoc,
  key: keyof IdentityView,
  value: string | string[],
): PersonaDoc {
  return { ...doc, identity: { ...asRecord(doc.identity), [key]: value } };
}

export function writeSelfFacts(
  doc: PersonaDoc,
  facts: SelfFactView[],
): PersonaDoc {
  return { ...doc, self_facts: facts };
}

export function writeWorldview(
  doc: PersonaDoc,
  claims: WorldviewView[],
): PersonaDoc {
  return { ...doc, worldview: claims };
}

export function writeStringList(
  doc: PersonaDoc,
  key: string,
  list: string[],
): PersonaDoc {
  return { ...doc, [key]: list };
}

// ----- routing (Spec 31): intelligent-routing config + budget caps -----
//
// Binds `routing.intelligent` (enabled + cost/quality/latency weights +
// fallback-on-miss) and `routing.budget` (per-turn/session/day cents caps) on
// the persona doc. Mirrors `RoutingConfig`/`ModelScoringWeights`/
// `RoutingBudgetConfig` in packages/core/src/persona/schema/persona.py.
//
// Non-experts pick an intent PRESET (D-31-3) that maps to the weight vector;
// the raw weights are an advanced escape hatch. The four preset vectors are
// anchored to the in-tree PROFILE_WEIGHTS (routing/scoring.py) — "balanced" is
// the ModelScoringWeights() default, "speed" mirrors the `voice` profile.

export interface ScoringWeights {
  cost: number;
  quality: number;
  latency: number;
}

export type RoutingPreset = "balanced" | "cost" | "quality" | "speed";

export interface RoutingBudgetView {
  /** null = unset (blank input ⇒ None, never 0 — D-31-X-empty-cap-input). */
  maxCentsPerTurn: number | null;
  maxCentsPerSession: number | null;
  maxCentsPerDay: number | null;
}

export interface RoutingView {
  intelligentEnabled: boolean;
  weights: ScoringWeights;
  fallbackOnMiss: boolean;
  budget: RoutingBudgetView;
}

/** The ModelScoringWeights() schema default (== the "balanced" preset). */
export const DEFAULT_SCORING_WEIGHTS: ScoringWeights = {
  cost: 0.4,
  quality: 0.5,
  latency: 0.1,
};

/** Locked preset → weight vectors (D-31-3); normalised to sum 1.0 for legibility. */
export const PRESET_WEIGHTS: Record<RoutingPreset, ScoringWeights> = {
  balanced: { cost: 0.4, quality: 0.5, latency: 0.1 },
  cost: { cost: 0.7, quality: 0.25, latency: 0.05 },
  quality: { cost: 0.15, quality: 0.8, latency: 0.05 },
  speed: { cost: 0.2, quality: 0.2, latency: 0.6 },
};

/** Chip display order for the preset selector ("prioritize cost / balanced / quality / speed"). */
export const ROUTING_PRESET_ORDER: RoutingPreset[] = [
  "cost",
  "balanced",
  "quality",
  "speed",
];

const PRESET_EPSILON = 1e-9;

/** Forward map: a preset → its (copied) weight vector. */
export function presetToWeights(preset: RoutingPreset): ScoringWeights {
  return { ...PRESET_WEIGHTS[preset] };
}

/**
 * Reverse map: a stored weight vector → the matching preset, or `"custom"` when
 * no preset matches exactly (ε 1e-9). Never normalises the input — a hand-edited
 * vector that isn't a preset reads honestly as "custom" (D-31-3).
 */
export function weightsToPreset(w: ScoringWeights): RoutingPreset | "custom" {
  const close = (a: number, b: number) => Math.abs(a - b) <= PRESET_EPSILON;
  for (const preset of ROUTING_PRESET_ORDER) {
    const p = PRESET_WEIGHTS[preset];
    if (
      close(w.cost, p.cost) &&
      close(w.quality, p.quality) &&
      close(w.latency, p.latency)
    ) {
      return preset;
    }
  }
  return "custom";
}

function asCapOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null;
}

/** Read the routing config, filling schema defaults for any absent field. */
export function readRouting(doc: PersonaDoc): RoutingView {
  const routing = asRecord(doc.routing);
  const intelligent = asRecord(routing.intelligent);
  const weights = asRecord(intelligent.weights);
  const budget = asRecord(routing.budget);
  return {
    intelligentEnabled: intelligent.enabled === true,
    weights: {
      cost: asNumber(weights.cost, DEFAULT_SCORING_WEIGHTS.cost),
      quality: asNumber(weights.quality, DEFAULT_SCORING_WEIGHTS.quality),
      latency: asNumber(weights.latency, DEFAULT_SCORING_WEIGHTS.latency),
    },
    // The schema default for fallback_to_rule_based_on_miss is True.
    fallbackOnMiss: intelligent.fallback_to_rule_based_on_miss !== false,
    budget: {
      maxCentsPerTurn: asCapOrNull(budget.max_cents_per_turn),
      maxCentsPerSession: asCapOrNull(budget.max_cents_per_session),
      maxCentsPerDay: asCapOrNull(budget.max_cents_per_day),
    },
  };
}

/**
 * Write the routing config back, preserving sibling keys (`tier_for_generation`,
 * `tier_for_tools`, and any unknown top-level keys). Unset budget caps (null)
 * are OMITTED from the YAML rather than written as `null`, and an all-unset
 * budget drops the `routing.budget` block entirely — keeping the doc minimal.
 */
export function writeRouting(doc: PersonaDoc, view: RoutingView): PersonaDoc {
  const intelligent: Record<string, unknown> = {
    enabled: view.intelligentEnabled,
    weights: {
      cost: view.weights.cost,
      quality: view.weights.quality,
      latency: view.weights.latency,
    },
    fallback_to_rule_based_on_miss: view.fallbackOnMiss,
  };
  const budget: Record<string, unknown> = {};
  if (view.budget.maxCentsPerTurn !== null) {
    budget.max_cents_per_turn = view.budget.maxCentsPerTurn;
  }
  if (view.budget.maxCentsPerSession !== null) {
    budget.max_cents_per_session = view.budget.maxCentsPerSession;
  }
  if (view.budget.maxCentsPerDay !== null) {
    budget.max_cents_per_day = view.budget.maxCentsPerDay;
  }
  const routing: Record<string, unknown> = {
    ...asRecord(doc.routing),
    intelligent,
  };
  if (Object.keys(budget).length > 0) {
    routing.budget = budget;
  } else {
    delete routing.budget;
  }
  return { ...doc, routing };
}
