import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// React Testing Library renders to document.body but doesn't auto-clean
// between tests (its built-in cleanup hook is opt-in for non-Jest runners).
// Without this, `screen.getByLabelText("Astrid")` collides across tests
// because the previous render is still in the DOM. Spec F1 T06 added the
// first test that uses screen-by-label queries, which surfaced the gap.
afterEach(cleanup);
