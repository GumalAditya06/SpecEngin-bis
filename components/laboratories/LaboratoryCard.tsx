import Link from "next/link";

import {
  capabilitiesForStandard,
  isRecognized,
  locationLabel,
  recognitionLabel,
} from "@/lib/laboratories/records";
import type { LaboratoryRecord } from "@/lib/laboratories/types";

/** Capability chips stay compact; the detail page carries the full list. */
const MAX_CHIPS = 3;

function StandardSummaryLink({
  record,
  standard,
}: {
  record: LaboratoryRecord;
  standard: LaboratoryRecord["standards"][number];
}) {
  const tests = capabilitiesForStandard(record, standard.id).length;
  return (
    <li>
      <Link
        href={`/standards/${standard.id}`}
        className="font-mono text-chroma hover:underline"
      >
        {standard.is_number}
      </Link>
      {tests > 0 && (
        <span className="ml-1 text-faint">
          {tests} test{tests === 1 ? "" : "s"}
        </span>
      )}
    </li>
  );
}

/**
 * A laboratory result in the directory.
 *
 * The hierarchy answers the directory question in order — is it recognized,
 * who is it, where is it, what can it test, and which standards does that
 * cover — before the single "View laboratory →" action. The card is not a
 * wrapper link because it contains its own links to the associated standards.
 */
export default function LaboratoryCard({
  record,
  href,
}: {
  record: LaboratoryRecord;
  href?: string;
}) {
  const target = href || `/laboratories/${record.id}`;
  const recognized = isRecognized(record.recognitionStatus);
  const chips = record.capabilities.slice(0, MAX_CHIPS);
  const overflow = record.capabilities.length - chips.length;
  return (
    <article className="panel laboratory-result-card">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <span
            className={`pill ${recognized ? "text-chroma" : "text-muted"}`}
            title="Recognition status"
          >
            <span className="sr-only">Recognition status: </span>
            {recognitionLabel(record.recognitionStatus)}
          </span>
          <h2 className="mt-3 text-lg font-medium">
            <Link href={target} className="hover:underline">
              {record.name}
            </Link>
          </h2>
          <p className="mt-1 text-sm text-muted">{locationLabel(record)}</p>
        </div>
        <p className="shrink-0 text-xs text-faint">
          {record.standardCount} standard{record.standardCount === 1 ? "" : "s"}
        </p>
      </div>

      <div className="mt-5">
        <p className="eyebrow">Testing capability</p>
        {!record.detailed ? (
          <p className="mt-2 text-sm text-faint">Loading capabilities…</p>
        ) : record.capabilities.length ? (
          <ul className="mt-2 flex flex-wrap gap-2">
            {chips.map((capability) => (
              <li key={capability.id} className="pill text-ink">
                {capability.name}
              </li>
            ))}
            {overflow > 0 && <li className="pill text-faint">+{overflow} more</li>}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-faint">
            No testing capability is recorded for this laboratory yet.
          </p>
        )}
      </div>

      <div className="mt-5 flex flex-wrap justify-between gap-3 border-t border-line pt-4 text-xs text-faint">
        {!record.detailed ? (
          <span aria-live="polite">Loading associated standards…</span>
        ) : record.standards.length ? (
          <ul className="flex flex-wrap gap-x-3 gap-y-1">
            {record.standards.map((standard) => (
              <StandardSummaryLink
                key={standard.id}
                record={record}
                standard={standard}
              />
            ))}
          </ul>
        ) : (
          <span>No standard is linked to this laboratory yet.</span>
        )}
        <Link href={target} className="text-ink">
          View laboratory →
        </Link>
      </div>
    </article>
  );
}
