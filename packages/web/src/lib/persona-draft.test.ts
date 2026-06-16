import { describe, expect, it } from "vitest";
import {
  DEFAULT_SCORING_WEIGHTS,
  docToYaml,
  type PersonaDoc,
  PRESET_WEIGHTS,
  presetToWeights,
  readIdentity,
  readRouting,
  readSelfFacts,
  readWorldview,
  weightsToPreset,
  writeIdentityField,
  writeRouting,
  writeSelfFacts,
  writeStringList,
  writeWorldview,
  yamlToDoc,
} from "./persona-draft";

const SAMPLE = `schema_version: "1.0"
identity:
  name: Astrid
  role: Tenancy assistant
  background: Helps tenants.
  language_default: en
  constraints:
    - Never give binding advice.
self_facts:
  - fact: Specialised in tenancy.
    confidence: 1
worldview:
  - claim: Tenants have rights.
    domain: tenancy
    epistemic: fact
    confidence: 0.95
    valid_time: always
tools:
  - web_search
skills: []
routing:
  tier_for_generation: auto
embedding:
  model: bge-small-en-v1.5
`;

describe("persona-draft round-trip", () => {
  it("parses YAML and reads structured fields", () => {
    const doc = yamlToDoc(SAMPLE);
    const id = readIdentity(doc);
    expect(id.name).toBe("Astrid");
    expect(id.constraints).toEqual(["Never give binding advice."]);
    expect(readSelfFacts(doc)[0]).toEqual({
      fact: "Specialised in tenancy.",
      confidence: 1,
    });
    expect(readWorldview(doc)[0].epistemic).toBe("fact");
  });

  it("survives a doc → yaml → doc round-trip", () => {
    const doc = yamlToDoc(SAMPLE);
    const round = yamlToDoc(docToYaml(doc));
    expect(round).toEqual(doc);
  });

  it("throws on invalid YAML and on a non-mapping top level", () => {
    expect(() => yamlToDoc("identity: : :")).toThrow();
    expect(() => yamlToDoc("- just\n- a\n- list")).toThrow();
  });
});

describe("persona-draft writers preserve sibling keys", () => {
  it("writeIdentityField keeps routing/embedding and other identity fields", () => {
    const doc = yamlToDoc(SAMPLE);
    const next = writeIdentityField(doc, "name", "Bjorn");
    expect(readIdentity(next).name).toBe("Bjorn");
    expect(readIdentity(next).role).toBe("Tenancy assistant"); // sibling kept
    expect((next as PersonaDoc).routing).toEqual(doc.routing); // top-level kept
    expect((next as PersonaDoc).embedding).toEqual(doc.embedding);
  });

  it("writeSelfFacts / writeWorldview / writeStringList replace only their slice", () => {
    const doc = yamlToDoc(SAMPLE);
    const a = writeSelfFacts(doc, [{ fact: "New fact.", confidence: 0.5 }]);
    expect(readSelfFacts(a)).toHaveLength(1);
    expect(readSelfFacts(a)[0].fact).toBe("New fact.");
    expect(a.routing).toEqual(doc.routing);

    const b = writeWorldview(doc, []);
    expect(readWorldview(b)).toEqual([]);

    const c = writeStringList(doc, "tools", ["web_search", "web_fetch"]);
    expect(c.tools).toEqual(["web_search", "web_fetch"]);
    expect(c.skills).toEqual([]);
  });

  it("invalid YAML in the editor leaves the prior doc usable (sync invariant)", () => {
    // Mirrors the editor's behaviour: a parse failure must not lose the form.
    const lastValid = yamlToDoc(SAMPLE);
    let doc = lastValid;
    try {
      doc = yamlToDoc("identity: : :");
    } catch {
      // keep last valid
    }
    expect(doc).toBe(lastValid);
  });
});

describe("persona-draft routing (Spec 31)", () => {
  it("readRouting fills schema defaults when routing is absent", () => {
    const doc = yamlToDoc(SAMPLE); // has only routing.tier_for_generation
    const r = readRouting(doc);
    expect(r.intelligentEnabled).toBe(false);
    expect(r.weights).toEqual(DEFAULT_SCORING_WEIGHTS);
    expect(r.fallbackOnMiss).toBe(true);
    expect(r.budget).toEqual({
      maxCentsPerTurn: null,
      maxCentsPerSession: null,
      maxCentsPerDay: null,
    });
  });

  it("readRouting reads an enabled persona with weights + budget caps", () => {
    const doc = yamlToDoc(`schema_version: "1.0"
routing:
  tier_for_generation: auto
  intelligent:
    enabled: true
    weights: { cost: 0.7, quality: 0.25, latency: 0.05 }
    fallback_to_rule_based_on_miss: false
  budget:
    max_cents_per_turn: 2.5
    max_cents_per_session: 50
`);
    const r = readRouting(doc);
    expect(r.intelligentEnabled).toBe(true);
    expect(r.weights).toEqual({ cost: 0.7, quality: 0.25, latency: 0.05 });
    expect(r.fallbackOnMiss).toBe(false);
    expect(r.budget.maxCentsPerTurn).toBe(2.5);
    expect(r.budget.maxCentsPerSession).toBe(50);
    expect(r.budget.maxCentsPerDay).toBeNull();
  });

  it("preset ↔ weights round-trips for every locked preset", () => {
    for (const preset of ["balanced", "cost", "quality", "speed"] as const) {
      expect(presetToWeights(preset)).toEqual(PRESET_WEIGHTS[preset]);
      expect(weightsToPreset(PRESET_WEIGHTS[preset])).toBe(preset);
    }
  });

  it("balanced preset equals the ModelScoringWeights() default", () => {
    expect(PRESET_WEIGHTS.balanced).toEqual(DEFAULT_SCORING_WEIGHTS);
    expect(weightsToPreset(DEFAULT_SCORING_WEIGHTS)).toBe("balanced");
  });

  it("weightsToPreset returns 'custom' for a non-preset vector (no normalisation)", () => {
    expect(weightsToPreset({ cost: 0.33, quality: 0.34, latency: 0.33 })).toBe(
      "custom",
    );
    // A vector that does not sum to 1 is still valid (D-23-1) and reads custom.
    expect(weightsToPreset({ cost: 1, quality: 1, latency: 1 })).toBe("custom");
  });

  it("writeRouting omits unset caps and drops an all-unset budget block", () => {
    const doc = yamlToDoc(SAMPLE);
    const next = writeRouting(doc, {
      intelligentEnabled: true,
      weights: presetToWeights("cost"),
      fallbackOnMiss: true,
      budget: {
        maxCentsPerTurn: 2.5,
        maxCentsPerSession: null,
        maxCentsPerDay: null,
      },
    });
    const routing = next.routing as Record<string, unknown>;
    // sibling tier key preserved
    expect(routing.tier_for_generation).toBe("auto");
    const budget = routing.budget as Record<string, unknown>;
    expect(budget).toEqual({ max_cents_per_turn: 2.5 }); // session/day omitted

    // clearing all caps drops the budget block entirely
    const cleared = writeRouting(next, {
      ...readRouting(next),
      budget: {
        maxCentsPerTurn: null,
        maxCentsPerSession: null,
        maxCentsPerDay: null,
      },
    });
    expect((cleared.routing as Record<string, unknown>).budget).toBeUndefined();
  });

  it("writeRouting → readRouting round-trips the view", () => {
    const doc = yamlToDoc(SAMPLE);
    const view = {
      intelligentEnabled: true,
      weights: presetToWeights("quality"),
      fallbackOnMiss: false,
      budget: {
        maxCentsPerTurn: 3,
        maxCentsPerSession: 40,
        maxCentsPerDay: null,
      },
    };
    expect(readRouting(writeRouting(doc, view))).toEqual(view);
  });

  it("an untouched routing section never rewrites the doc (controlled-form invariant)", () => {
    // The form only calls writeRouting on user interaction; reading must be pure.
    const doc = yamlToDoc(SAMPLE);
    readRouting(doc);
    expect(yamlToDoc(docToYaml(doc))).toEqual(doc);
  });
});
