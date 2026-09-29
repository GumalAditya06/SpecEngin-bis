import Link from "next/link";

import { capabilitiesForStandard } from "@/lib/laboratories/records";
import type {
  LaboratoryCapability,
  LaboratoryRecord,
} from "@/lib/laboratories/types";

/**
 * The relationship card: standard → testing capability → laboratory.
 *
 * A plain list of standards does not answer "can this laboratory test my
 * product against IS 4250?", so the capability this laboratory performs for
 * that specific standard sits inside the standard's own card, with the
 * laboratory named underneath.
 */
export default function LaboratoryStandardCard({
  record,
  standard,
}: {
  record: LaboratoryRecord;
  standard: LaboratoryRecord["standards"][number];
}) {
  const capabilities = capabilitiesForStandard(record, standard.id);
  return (
    <article className="panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-sm text-chroma">{standard.is_number}</p>
          <h3 className="mt-2 text-lg font-medium">
            {standard.title || "Untitled standard"}
          </h3>
        </div>
        {standard.id && (
          <Link className="button shrink-0" href={`/standards/${standard.id}`}>
            View standard →
          </Link>
        )}
      </div>

      <div className="mt-5 border-t border-line pt-4">
        <p className="eyebrow">Testing capability</p>
        {capabilities.length ? (
          <ul className="mt-2 flex flex-wrap gap-2">
            {capabilities.map((capability: LaboratoryCapability) => (
              <li key={capability.id} className="pill text-ink">
                {capability.name}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm leading-7 text-muted">
            No capability is recorded for this standard. Ask which tests this
            laboratory performs before committing a sample.
          </p>
        )}
        <p className="mt-4 text-xs text-faint">
          Tested at {record.name}
          {record.city ? `, ${record.city}` : ""}
        </p>
      </div>
    </article>
  );
}

/** Every standard the laboratory is recognized for, as relationship cards. */
export function LaboratoryStandardList({
  record,
}: {
  record: LaboratoryRecord;
}) {
  if (!record.standards.length)
    return (
      <p className="text-sm leading-7 text-muted">
        No standard is linked to this laboratory yet, so no testing relationship
        can be shown.
      </p>
    );
  return (
    <div className="grid gap-4">
      {record.standards.map((standard) => (
        <LaboratoryStandardCard
          key={standard.id}
          record={record}
          standard={standard}
        />
      ))}
    </div>
  );
}
