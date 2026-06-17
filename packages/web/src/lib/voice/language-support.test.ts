import { describe, expect, it } from "vitest";
import { voiceLanguageWarning } from "./language-support";

describe("voiceLanguageWarning (Spec 32 D author-time hint)", () => {
  it("is silent for served languages and their variants", () => {
    expect(voiceLanguageWarning("en")).toBeNull();
    expect(voiceLanguageWarning("no")).toBeNull();
    expect(voiceLanguageWarning("nb")).toBeNull(); // collapses to no
    expect(voiceLanguageWarning("nn-NO")).toBeNull();
    expect(voiceLanguageWarning("en-US")).toBeNull();
    expect(voiceLanguageWarning("de-CH")).toBeNull(); // base-code fallback
    expect(voiceLanguageWarning("ar")).toBeNull();
  });

  it("is silent while the field is blank (don't nag mid-typing)", () => {
    expect(voiceLanguageWarning("")).toBeNull();
    expect(voiceLanguageWarning("   ")).toBeNull();
  });

  it("warns for an unsupported language, naming it and the English fallback", () => {
    const w = voiceLanguageWarning("klingon");
    expect(w).not.toBeNull();
    expect(w).toContain("klingon");
    expect(w).toContain("English");
  });
});
