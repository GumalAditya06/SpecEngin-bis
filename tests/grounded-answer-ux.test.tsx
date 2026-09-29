import { readFileSync } from "node:fs";
import { join } from "node:path";
import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";

import AssistantPage from "@/app/assistant/content";
import Answer from "@/components/assistant/Answer";
import SourceDrawer from "@/components/sources/SourceDrawer";
import type { AssistantCitation, AssistantResponse } from "@/lib/api";
import { sourceFromCitation } from "@/lib/sources/records";

const assistantQueryStream = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/client", () => ({ assistantQueryStream }));

const evidenceText = "A control unit shall preserve this exact BIS evidence text.\nSecond line.";

function citation(overrides: Partial<AssistantCitation> = {}): AssistantCitation {
  return {
    evidence_id: "E1",
    source_id: "source-1",
    document_id: "document-1",
    title: "Control unit requirements",
    version: "2025 edition",
    clause: "4.2",
    page: 7,
    url: "https://www.bis.gov.in/bis-source.pdf",
    excerpt: evidenceText,
    standard_number: "IS 4250:2025",
    section: "Control units",
    licence_class: null,
    ...overrides,
  };
}

function response(overrides: Partial<AssistantResponse> = {}): AssistantResponse {
  return {
    answer: "A control unit is covered by the stated requirement [E1].",
    sections: [],
    standards: [],
    attributes: [],
    route: [],
    laboratories: [],
    certification: [],
    testing: [],
    next_steps: [],
    citations: [citation()],
    confidence_state: "VERIFIED_EVIDENCE",
    clarification_question: null,
    ...overrides,
  };
}

function DrawerHarness({ item = citation() }: { item?: AssistantCitation }) {
  const [open, setOpen] = useState(false);
  return <>
    <button onClick={() => setOpen(true)}>Open evidence</button>
    <SourceDrawer
      source={open ? sourceFromCitation(item) : null}
      clause={item.clause}
      page={item.page}
      section={item.section}
      excerpt={item.excerpt}
      onClose={() => setOpen(false)}
    />
  </>;
}

beforeEach(() => {
  assistantQueryStream.mockReset();
  localStorage.clear();
});

describe("grounded answer states and citations", () => {
  test("renders VERIFIED_EVIDENCE with an interactive inline citation", async () => {
    const onSource = vi.fn();
    render(<Answer result={response()} onSource={onSource} />);
    expect(screen.getByLabelText("Evidence status: Verified evidence")).toBeDefined();
    const reference = screen.getByRole("button", { name: /View evidence \[E1\]/ });
    await userEvent.click(reference);
    expect(onSource).toHaveBeenCalledWith(expect.objectContaining({ evidence_id: "E1" }));
  });

  test("renders PARTIAL_EVIDENCE and preserves backend limitations", () => {
    render(<Answer result={response({ confidence_state: "PARTIAL_EVIDENCE", next_steps: ["The source does not establish the installation threshold."] })} onSource={vi.fn()} />);
    expect(screen.getByLabelText("Evidence status: Partial evidence")).toBeDefined();
    expect(screen.getByRole("heading", { name: "Limitations" })).toBeDefined();
    expect(screen.getByText("The source does not establish the installation threshold.")).toBeDefined();
  });

  test("renders NO_VERIFIED_EVIDENCE without stale citations", () => {
    render(<Answer result={response({ answer: "No verified evidence was found for this question.", confidence_state: "NO_VERIFIED_EVIDENCE" })} onSource={vi.fn()} />);
    expect(screen.getByText("No verified evidence was found for this question.")).toBeDefined();
    expect(screen.queryByRole("heading", { name: "Sources / Evidence" })).toBeNull();
    expect(screen.queryByRole("button", { name: /View evidence/ })).toBeNull();
  });

  test("supports one and multiple adjacent citations in answer order", () => {
    const second = citation({ evidence_id: "E2", clause: "5", page: 8 });
    render(<Answer result={response({ answer: "Supported jointly [E1][E2].", citations: [citation(), second] })} onSource={vi.fn()} />);
    const controls = screen.getAllByRole("button", { name: /View evidence \[E[12]\]/ });
    expect(controls.slice(0, 2).map((item) => item.textContent)).toEqual(["[E1]", "[E2]"]);
    expect(screen.getAllByRole("article")).toHaveLength(2);
  });

  test("deduplicates evidence cards while preserving repeated inline references", () => {
    const item = citation();
    render(<Answer result={response({ answer: "First [E1], then again [E1].", citations: [item, { ...item }] })} onSource={vi.fn()} />);
    expect(screen.getAllByRole("button", { name: /View evidence \[E1\]/ })).toHaveLength(2);
    expect(screen.getAllByRole("article")).toHaveLength(1);
  });

  test("leaves unsupported citation-like text non-interactive", () => {
    render(<Answer result={response({ answer: "Validated [E1], unsupported [E99]." })} onSource={vi.fn()} />);
    expect(screen.getByText("[E99]")).toBeDefined();
    expect(screen.queryByRole("button", { name: /E99/ })).toBeNull();
  });

  test("renders citation metadata and omits unavailable optional metadata", () => {
    render(<Answer result={response({ citations: [citation({ version: null, page: null, section: null, title: null })] })} onSource={vi.fn()} />);
    expect(screen.getAllByText("IS 4250:2025").length).toBeGreaterThan(0);
    expect(screen.getByText("4.2")).toBeDefined();
    expect(screen.queryByText("Version")).toBeNull();
    expect(screen.queryByText("Page")).toBeNull();
    expect(screen.queryByText("Section")).toBeNull();
  });

  test("citation controls are native keyboard controls with accessible names", async () => {
    const onSource = vi.fn();
    render(<Answer result={response()} onSource={onSource} />);
    const reference = screen.getByRole("button", { name: /View evidence \[E1\]: IS 4250:2025, Clause 4.2, page 7/ });
    reference.focus();
    await userEvent.keyboard("{Enter}");
    expect(onSource).toHaveBeenCalledOnce();
  });
});

describe("shared source drawer", () => {
  test("opens, shows exact evidence and validated source metadata, then closes", async () => {
    render(<DrawerHarness />);
    const trigger = screen.getByRole("button", { name: "Open evidence" });
    await userEvent.click(trigger);
    expect(await screen.findByRole("dialog", { name: "Source" })).toBeDefined();
    expect(screen.getByLabelText("Retrieved evidence text").textContent).toBe(evidenceText);
    expect(screen.getAllByText("IS 4250:2025").length).toBeGreaterThan(0);
    expect(screen.getAllByText("4.2").length).toBeGreaterThan(0);
    expect(screen.getByText("7")).toBeDefined();
    const original = screen.getByRole("link", { name: "Open original source →" });
    expect(original.getAttribute("href")).toBe("https://www.bis.gov.in/bis-source.pdf");
    await userEvent.click(screen.getByRole("button", { name: "Close source panel" }));
    expect(screen.queryByRole("dialog", { name: "Source" })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  test("hides original-source action when the backend URL is absent", async () => {
    render(<DrawerHarness item={citation({ url: null })} />);
    await userEvent.click(screen.getByRole("button", { name: "Open evidence" }));
    expect(screen.queryByRole("link", { name: "Open original source →" })).toBeNull();
  });

  test("retains responsive no-overflow and mobile drawer rules", () => {
    const css = readFileSync(join(process.cwd(), "components/assistant/assistant.css"), "utf8");
    const globalCss = readFileSync(join(process.cwd(), "app/globals.css"), "utf8");
    const drawer = readFileSync(join(process.cwd(), "components/sources/SourceDrawer.tsx"), "utf8");
    expect(css).toContain("@media (max-width: 639px)");
    expect(css).toContain(".citation-metadata { grid-template-columns: 1fr; }");
    expect(css).toContain("overflow-wrap: anywhere");
    expect(globalCss).toContain(".evidence-drawer { position: absolute; inset: auto 0 0;");
    expect(globalCss).toContain("max-height: 85dvh");
    expect(drawer).toContain("w-full max-w-[480px]");
  });
});

describe("assistant request states", () => {
  test("completes the query-to-citation-to-source-drawer flow", async () => {
    assistantQueryStream.mockImplementation(async (_payload: unknown, onStage: (stage: "evidence", message: string) => void) => {
      onStage("evidence", "Grounded answer ready");
      return response();
    });
    render(<AssistantPage initialQuery="What is the definition of a control unit?" />);
    fireEvent.click(screen.getByRole("button", { name: /^Ask/ }));
    const reference = await screen.findByRole("button", { name: /View evidence \[E1\]/ });
    await userEvent.click(reference);
    expect(await screen.findByRole("dialog", { name: "Source" })).toBeDefined();
    expect(screen.getByLabelText("Retrieved evidence text").textContent).toBe(evidenceText);
    expect(screen.getAllByText("IS 4250:2025").length).toBeGreaterThan(0);
    expect(screen.getAllByText("4.2").length).toBeGreaterThan(0);
    expect(screen.getAllByText("7").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Open original source →" }).getAttribute("href")).toBe("https://www.bis.gov.in/bis-source.pdf");
  });

  test("keeps the existing loading treatment while generation is pending", async () => {
    assistantQueryStream.mockImplementation(() => new Promise(() => undefined));
    render(<AssistantPage initialQuery="What is the control unit requirement?" />);
    fireEvent.click(screen.getByRole("button", { name: /^Ask/ }));
    expect(await screen.findByRole("status")).toBeDefined();
    expect(screen.getByText("The answer will appear when the evidence is ready.")).toBeDefined();
  });

  test("uses the existing controlled error treatment", async () => {
    assistantQueryStream.mockRejectedValue(new Error("The grounded answer could not be generated. Please try again."));
    render(<AssistantPage initialQuery="What is the control unit requirement?" />);
    fireEvent.click(screen.getByRole("button", { name: /^Ask/ }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeDefined());
    expect(screen.getByText("The research could not be completed.")).toBeDefined();
    expect(screen.getByText("The grounded answer could not be generated. Please try again.")).toBeDefined();
  });

  test("clears prior evidence before a later no-evidence response", async () => {
    assistantQueryStream
      .mockResolvedValueOnce(response())
      .mockResolvedValueOnce(response({
        answer: "No verified evidence was found for this query.",
        citations: [],
        confidence_state: "NO_VERIFIED_EVIDENCE",
      }));
    render(<AssistantPage initialQuery="What is the control unit requirement?" />);
    await userEvent.click(screen.getByRole("button", { name: /^Ask/ }));
    expect(await screen.findByRole("button", { name: /View evidence \[E1\]/ })).toBeDefined();

    await userEvent.click(screen.getByRole("button", { name: /New question/ }));
    const input = screen.getByLabelText("Describe your product or ask a question");
    await userEvent.type(input, "What is the weather in Pune?");
    await userEvent.click(screen.getByRole("button", { name: /^Ask/ }));

    expect(await screen.findByText("No verified evidence was found for this query.")).toBeDefined();
    expect(screen.queryByRole("button", { name: /View evidence/ })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Sources / Evidence" })).toBeNull();
  });

  test("matches the backend 2000-character query contract", () => {
    render(<AssistantPage />);
    expect(screen.getByLabelText("Describe your product or ask a question").getAttribute("maxlength")).toBe("2000");
  });

  test("all source-bearing product contexts import the shared drawer", () => {
    for (const file of [
      "app/assistant/content.tsx",
      "app/sources/[id]/content.tsx",
      "app/standards/[id]/content.tsx",
      "app/laboratories/[id]/content.tsx",
    ]) {
      expect(readFileSync(join(process.cwd(), file), "utf8")).toContain("components/sources/SourceDrawer");
    }
    const services = readFileSync(join(process.cwd(), "app/services/page.tsx"), "utf8");
    expect(services).toContain('href: "/sources"');
  });
});
