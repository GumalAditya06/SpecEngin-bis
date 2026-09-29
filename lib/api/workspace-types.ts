/**
 * Compliance-case domain model.
 *
 * No screen renders these types in the current MVP — the workspace was removed
 * from the product flow. The backend still exposes `/api/v1/compliance-cases`,
 * so the shape is kept here as the contract for that endpoint: re-adding case
 * work is a routing change, not a re-derivation of the model.
 */
import type { ComplianceCase } from "./types";

export interface CaseTask {
  id: string;
  title: string;
  done: boolean;
}
export interface CaseEvidence {
  id: string;
  name: string;
  url: string;
  added_at: string;
}
export interface CaseNote {
  id: string;
  text: string;
  created_at: string;
}
export interface CaseActivity {
  id: string;
  text: string;
  created_at: string;
}
export interface CaseDetail extends ComplianceCase {
  query: string;
  owner: string;
  due_date: string;
  laboratory_id: string | null;
  tasks: CaseTask[];
  evidence: CaseEvidence[];
  notes: CaseNote[];
  activity: CaseActivity[];
}
export interface CasePatch {
  status?: ComplianceCase["status"];
  owner?: string;
  due_date?: string;
  laboratory_id?: string | null;
  task?: { id: string; done: boolean };
  note?: string;
  evidence?: { name: string; url: string };
}
