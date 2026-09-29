"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useState } from "react";

import LaboratoryCapabilities from "@/components/laboratories/LaboratoryCapabilities";
import { LaboratoryStandardList } from "@/components/laboratories/LaboratoryStandardCard";
import SourceDrawer from "@/components/sources/SourceDrawer";
import SourceList from "@/components/sources/SourceList";
import type { LaboratoryRecord } from "@/lib/laboratories/types";
import {
  isRecognized,
  laboratoryAssistantHref,
  laboratoryAssistantLabel,
  locationLabel,
  recognitionFacts,
  recognitionLabel,
} from "@/lib/laboratories/records";
import type { SourceRecord } from "@/lib/sources/types";

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="standard-editorial-section">
      <h2>{title}</h2>
      <div className="standard-section-body">{children}</div>
    </section>
  );
}

/**
 * A laboratory, read as the chain the product is actually about:
 * standard, then the capability it can test, then this laboratory.
 *
 * Everything on the page comes from the laboratory detail payload, so a missing
 * overview or an unrecorded recognition scope is omitted rather than filled in.
 * Sources are the shared Part 5 records opened in the shared drawer.
 */
export default function LaboratoryDetail({
  laboratory,
  sources,
}: {
  laboratory: LaboratoryRecord;
  sources: SourceRecord[];
}) {
  const [openSource, setOpenSource] = useState<SourceRecord | null>(null);
  return (
    <section className="page-wrap standard-detail">
      <Link
        href="/laboratories"
        className="text-sm text-muted transition-colors hover:text-ink"
      >
        ← All laboratories
      </Link>

      <header className="standard-detail-hero">
        <div>
          <p className="eyebrow">Testing laboratory</p>
          <h1>{laboratory.name}</h1>
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <span
              className={`pill ${
                isRecognized(laboratory.recognitionStatus)
                  ? "text-chroma"
                  : "text-muted"
              }`}
            >
              <span className="sr-only">Recognition status: </span>
              {recognitionLabel(laboratory.recognitionStatus)}
            </span>
            <span className="text-sm text-muted">
              {locationLabel(laboratory)}
            </span>
          </div>
          {laboratory.overview && (
            <p className="standard-lede mt-6">{laboratory.overview}</p>
          )}
        </div>
        <div className="standard-detail-actions">
          <Link
            className="button button-primary"
            href={laboratoryAssistantHref(laboratory)}
            // A `title` rather than an `aria-label`: the accessible name stays
            // the visible "Ask Assistant about testing", and the fuller context
            // is supplementary (WCAG label-in-name).
            title={laboratoryAssistantLabel(laboratory)}
          >
            Ask Assistant about testing →
          </Link>
        </div>
      </header>

      <nav className="standard-section-nav" aria-label="Laboratory sections">
        {[
          ["capabilities", "Testing capabilities"],
          ["standards", "Associated standards"],
          ["recognition", "Recognition"],
          ["sources", "Sources"],
        ].map(([href, label]) => (
          <a key={href} href={`#${href}`}>
            {label}
          </a>
        ))}
      </nav>

      <div className="standard-editorial">
        <Section id="capabilities" title="Testing capabilities">
          <LaboratoryCapabilities record={laboratory} />
        </Section>

        <Section id="standards" title="Associated standards">
          <p className="standard-lede">
            For each standard, what this laboratory is recorded as testing.
          </p>
          <div className="mt-6">
            <LaboratoryStandardList record={laboratory} />
          </div>
        </Section>

        <Section id="recognition" title="Recognition information">
          <dl className="standard-facts">
            {recognitionFacts(laboratory).map((fact) => (
              <div key={fact.label}>
                <dt>{fact.label}</dt>
                <dd>{fact.value}</dd>
              </div>
            ))}
          </dl>
        </Section>

        <Section id="sources" title="Relevant sources">
          <p className="standard-lede">
            Source documents behind the standards this laboratory is recognized
            for.
          </p>
          <div className="mt-6">
            <SourceList
              sources={sources}
              onOpen={setOpenSource}
              empty="No source documents are indexed for the associated standards yet."
            />
          </div>
        </Section>
      </div>

      <SourceDrawer
        source={openSource}
        onClose={() => setOpenSource(null)}
      />
    </section>
  );
}
