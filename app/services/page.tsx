import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "BIS services — Specengine BIS",
  description:
    "What Specengine covers across a BIS service route: standard identification, scope and requirements, source evidence, testing and laboratories.",
};

/**
 * The information side of a BIS service route.
 *
 * Every entry describes research the product actually performs and links to the
 * screen that performs it, so the page stays a directory rather than a set of
 * promises — Specengine informs the work, it does not submit anything to BIS.
 */
const stages = [
  {
    step: "01",
    name: "Understand the product",
    text: "Describe the product, its material, construction and intended use so the research starts with the right context.",
    href: "/assistant",
    cta: "Ask the Assistant",
  },
  {
    step: "02",
    name: "Find the Indian Standard",
    text: "Search candidate Indian Standards by product or IS number, then review scope and status before selecting one.",
    href: "/standards",
    cta: "Explore Standards",
  },
  {
    step: "03",
    name: "Verify the source",
    text: "Follow a citation to the document behind it. Inspect available clause text, publisher, edition and access class instead of relying on a summary.",
    href: "/sources",
    cta: "Open Sources",
  },
  {
    step: "04",
    name: "Understand requirements & certification",
    text: "Bring product requirements, marking and the certification route into focus, with supporting references for each conclusion.",
    href: "/assistant?q=What%20requirements%20and%20BIS%20certification%20route%20apply%20to%20my%20product%3F",
    cta: "Ask about requirements",
  },
  {
    step: "05",
    name: "Find testing guidance",
    text: "Identify the test methods and performance requirements linked to the standard before choosing a laboratory.",
    href: "/assistant?q=What%20testing%20is%20required%20for%20my%20product%3F",
    cta: "Ask about testing",
  },
  {
    step: "06",
    name: "Find a laboratory",
    text: "Match the standard and testing capability to laboratory listings, location and recorded recognition details.",
    href: "/laboratories",
    cta: "Find a laboratory",
  },
];

const limits = [
  "No application, licence or registration request is submitted to BIS.",
  "No laboratory testing is booked and no enquiry is sent on your behalf.",
  "No certification decision is recorded. Applicability is confirmed against the source document.",
];

export default function ServicesPage() {
  return (
    <section className="page-wrap">
      <p className="eyebrow">BIS services</p>
      <h1 className="page-heading mt-3">
        Understand the route.
        <br />
        <span className="text-muted">Then verify it against the source.</span>
      </h1>
      <p className="mt-4 max-w-2xl text-base leading-7 text-muted">
        Specengine covers the research behind a BIS service route: which Indian
        Standard applies, what it requires, where the evidence sits, and who can
        test against it. Start at the step that matches where you are.
      </p>

      <ol className="mt-10 grid gap-4 md:grid-cols-2">
        {stages.map((stage) => (
          <li key={stage.step} className="panel flex flex-col">
            <span className="font-mono text-xs text-chroma">{stage.step}</span>
            <h2 className="mt-5 text-lg font-medium">{stage.name}</h2>
            <p className="mt-3 text-sm leading-7 text-muted">{stage.text}</p>
            <Link
              className="button mt-6 self-start"
              href={stage.href}
            >
              {stage.cta}
              <span className="sr-only">: {stage.name}</span>
            </Link>
          </li>
        ))}
      </ol>

      <div className="panel mt-4">
        <h2 className="text-lg font-medium">What this prototype does not do</h2>
        <ul className="mt-4 space-y-3 text-sm leading-7 text-muted">
          {limits.map((limit) => (
            <li key={limit} className="flex gap-3">
              <span className="mt-2.5 h-1 w-1 shrink-0 rounded-full bg-chroma" aria-hidden />
              {limit}
            </li>
          ))}
        </ul>
        <p className="mt-6 border-t border-line pt-4 text-xs text-faint">
          Prototype build. The BIS catalogue is sample data, and no request
          leaves this browser.
        </p>
      </div>

      <div className="mt-8 flex flex-wrap gap-3">
        <Link className="button button-primary" href="/assistant">
          Ask the Assistant
        </Link>
        <Link className="button" href="/standards">
          Explore standards
        </Link>
      </div>
    </section>
  );
}
