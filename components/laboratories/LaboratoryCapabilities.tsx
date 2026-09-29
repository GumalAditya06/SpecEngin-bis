import Link from "next/link";

import { capabilityGroupLabel, capabilityGroups } from "@/lib/laboratories/records";
import type { LaboratoryRecord } from "@/lib/laboratories/types";

/**
 * The laboratory's capability inventory, grouped by the standard each
 * capability is recorded against.
 *
 * The grouping is the data's own (`tests.standard_id`) rather than an editorial
 * taxonomy, so the list stays truthful as the catalogue grows. Capabilities
 * with no standard link are listed last instead of being dropped.
 */
export default function LaboratoryCapabilities({
  record,
}: {
  record: LaboratoryRecord;
}) {
  if (!record.capabilities.length)
    return (
      <p className="text-sm leading-7 text-muted">
        No testing capability is recorded for this laboratory yet. Confirm the
        current scope with the laboratory before arranging a test.
      </p>
    );
  const groups = capabilityGroups(record);
  return (
    <div className="grid gap-3">
      {groups.map((group) => {
        const standard = group.standard;
        return (
          <div key={standard?.id ?? "unlinked"} className="standard-inline-card">
            <p className="font-mono text-xs text-chroma">
              {standard?.id ? (
                <Link href={`/standards/${standard.id}`} className="hover:underline">
                  {capabilityGroupLabel(group)}
                </Link>
              ) : (
                capabilityGroupLabel(group)
              )}
            </p>
            <ul className="mt-1 grid gap-1">
              {group.capabilities.map((capability) => (
                <li key={capability.id} className="text-sm text-ink">
                  {capability.name}
                  {capability.description && (
                    <span className="mt-1 block text-xs text-muted">
                      {capability.description}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}
