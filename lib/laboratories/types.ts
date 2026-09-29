// Frontend abstraction over the laboratory layer.
//
// The product question is "which laboratory can test this product against
// this standard?", so every laboratory surface reads a single
// `LaboratoryRecord` that makes the standard → capability relationship
// explicit. Nothing here invents data: a field is populated only from what
// `GET /api/v1/laboratories` and `GET /api/v1/laboratories/:id` already
// return. A field the backend cannot supply stays `null` — or `undefined` when
// the relationship itself is unknowable — so the UI hides it instead of
// guessing.

import type { RelatedStandardRef } from "@/lib/sources/types";

/** A test a laboratory is recorded as performing (`tests.test_name`). */
export interface LaboratoryCapability {
  id: string;
  name: string;
  description: string | null;
  /** Standard the capability is recorded against, when the backend links one. */
  standardId: string | null;
}

/**
 * Recognition metadata BIS records beyond the status: recognition number,
 * approved scope, validity dates. The `laboratories` model carries none of
 * these, so this is `undefined` today and the recognition section renders
 * around it. Return a populated object once the backend exposes the columns
 * and the extra fields appear without any component changing.
 */
export interface LaboratoryRecognitionDetail {
  reference: string | null;
  scope: string | null;
  validFrom: string | null;
  validUntil: string | null;
}

export interface LaboratoryRecord {
  id: string;
  name: string;
  city: string | null;
  state: string | null;
  address: string | null;
  contactEmail: string | null;
  /** Raw `recognition_status`, e.g. "recognized". */
  recognitionStatus: string;
  /** `standard_count` — present on the list payload, so always available. */
  standardCount: number;
  /**
   * True once the detail payload has been loaded. The list endpoint returns
   * neither `standards` nor `tests`, so an undetailed record only carries its
   * count and must not be read as "this laboratory has no capabilities".
   */
  detailed: boolean;
  standards: RelatedStandardRef[];
  capabilities: LaboratoryCapability[];
  /**
   * Laboratory overview. The `laboratories` table has no description column,
   * so this is `null` until the backend exposes one; the detail page simply
   * omits the paragraph until then.
   */
  overview: string | null;
  /** `undefined` = not knowable yet. See `LaboratoryRecognitionDetail`. */
  recognitionDetail?: LaboratoryRecognitionDetail;
}

/** Capabilities grouped by the standard they are recorded against. */
export interface LaboratoryCapabilityGroup {
  /** Standard the group belongs to; `null` for capabilities with no link. */
  standard: RelatedStandardRef | null;
  capabilities: LaboratoryCapability[];
}

/** A standard in a laboratory filter, with how many laboratories list it. */
export interface LaboratoryStandardOption {
  id: string;
  isNumber: string;
  title: string | null;
  laboratoryCount: number;
}
