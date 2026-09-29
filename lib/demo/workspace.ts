/**
 * Demo-mode case store: a browser-local CRUD adapter over `localStorage`.
 *
 * Nothing renders cases in the current MVP, so no request reaches this module.
 * It is kept because the adapter pattern (seed data, validated read, patch,
 * persistence key) is reusable for any future state that has to survive a
 * reload in demo mode, and because `lib/api/client.ts` still exposes the
 * matching backend endpoints.
 */
import { standards } from "./catalogue";
import type { CaseDetail, CasePatch } from "../api/workspace-types";

const KEY = "specengine-demo-cases-v1";
const tasks = () =>
  [
    "Confirm product scope and applicable standards",
    "Review certification scheme and required documents",
    "Select a laboratory and confirm testing scope",
    "Collect test reports and application evidence",
    "Review the application and record the outcome",
  ].map((title, i) => ({ id: `task-${i + 1}`, title, done: false }));
const now = () => new Date().toISOString();
function activity(text: string) {
  return { id: crypto.randomUUID(), text, created_at: now() };
}
function initialCases(): CaseDetail[] {
  return [
    {
      id: "demo-wire-rope",
      product: "Steel wire rope · manufacturing",
      status: "in_progress",
      created_at: "2026-09-23T09:00:00Z",
      standards: standards
        .slice(0, 1)
        .map((s) => ({ id: s.id, is_number: s.is_number, title: s.title })),
      query: "I manufacture steel wire ropes in India",
      owner: "Product team",
      due_date: "",
      laboratory_id: null,
      tasks: tasks().map((t, i) => ({ ...t, done: i === 0 })),
      evidence: [],
      notes: [],
      activity: [
        {
          id: "demo-start",
          text: "Sample case created",
          created_at: "2026-09-23T09:00:00Z",
        },
      ],
    },
  ];
}
export function readCases(): CaseDetail[] {
  if (typeof window === "undefined") return initialCases();
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return initialCases();
    const parsed: unknown = JSON.parse(raw);
    if (
      !Array.isArray(parsed) ||
      !parsed.every(
        (c) =>
          c &&
          typeof c.id === "string" &&
          Array.isArray(c.tasks) &&
          Array.isArray(c.activity),
      )
    )
      throw new Error();
    return parsed as CaseDetail[];
  } catch {
    throw new Error(
      "Saved demo cases could not be read. Check browser storage permissions or clear this site's demo storage.",
    );
  }
}
function save(cases: CaseDetail[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(cases));
  } catch {
    throw new Error(
      "Your changes could not be saved. Browser storage may be full or disabled.",
    );
  }
}
export function createCase(body: {
  product: string;
  standard_ids: string[];
  query?: string;
}): CaseDetail {
  if (!body.product.trim()) throw new Error("Enter a product name.");
  const item: CaseDetail = {
    id: crypto.randomUUID(),
    product: body.product.trim(),
    status: "open",
    created_at: now(),
    standards: standards
      .filter((s) => body.standard_ids.includes(s.id))
      .map((s) => ({ id: s.id, is_number: s.is_number, title: s.title })),
    query: body.query || "",
    owner: "",
    due_date: "",
    laboratory_id: null,
    tasks: tasks(),
    evidence: [],
    notes: [],
    activity: [activity("Case created")],
  };
  save([item, ...readCases()]);
  return item;
}
export function patchCase(id: string, patch: CasePatch): CaseDetail {
  const cases = readCases();
  const item = cases.find((c) => c.id === id);
  if (!item) throw new Error("Case not found.");
  let message = "Case details updated";
  if (patch.status !== undefined) {
    item.status = patch.status;
    message = `Status changed to ${patch.status.replace("_", " ")}`;
  }
  if (patch.owner !== undefined) item.owner = patch.owner.trim();
  if (patch.due_date !== undefined) item.due_date = patch.due_date;
  if (patch.laboratory_id !== undefined) {
    item.laboratory_id = patch.laboratory_id;
    message = "Laboratory selection updated";
  }
  if (patch.task) {
    const task = item.tasks.find((t) => t.id === patch.task!.id);
    if (!task) throw new Error("Task not found.");
    task.done = patch.task.done;
    message = `${task.done ? "Completed" : "Reopened"}: ${task.title}`;
  }
  if (patch.note?.trim()) {
    item.notes.unshift({
      id: crypto.randomUUID(),
      text: patch.note.trim(),
      created_at: now(),
    });
    message = "Note added";
  }
  if (patch.evidence) {
    const url = new URL(patch.evidence.url);
    if (!["http:", "https:"].includes(url.protocol))
      throw new Error("Use an http or https evidence link.");
    item.evidence.unshift({
      id: crypto.randomUUID(),
      name: patch.evidence.name.trim(),
      url: url.href,
      added_at: now(),
    });
    message = "Evidence link added";
  }
  item.activity.unshift(activity(message));
  save(cases);
  return item;
}
