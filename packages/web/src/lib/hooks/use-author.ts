"use client";

import { useCallback } from "react";
import { type AuthoringDraft, unwrap } from "@/lib/api";
import { useApi } from "@/lib/api/use-api";

/**
 * The authoring seam (spec 10, D-10-2). `POST /v1/personas/author` now returns a
 * DRAFT envelope (`{yaml, questions, prompt_version}`) — it does NOT create a
 * persona. The wizard reviews/refines the draft, then saves via `POST
 * /v1/personas`. `refine` answers one clarifying question → a re-generated draft;
 * the 3-round cap is owned by the wizard (the server backstops it, D-10-5).
 */
export function useAuthor() {
  const api = useApi();
  const author = useCallback(
    async (description: string): Promise<AuthoringDraft> =>
      unwrap(await api.POST("/v1/personas/author", { body: { description } })),
    [api],
  );
  const refine = useCallback(
    async (args: {
      currentYaml: string;
      question: string;
      answer: string;
      round: number;
    }): Promise<AuthoringDraft> =>
      unwrap(
        await api.POST("/v1/personas/author/refine", {
          body: {
            current_yaml: args.currentYaml,
            question: args.question,
            answer: args.answer,
            round: args.round,
          },
        }),
      ),
    [api],
  );
  return { author, refine };
}
