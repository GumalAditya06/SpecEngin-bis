import "./landing.css";
import Link from "next/link";
import HeroMesh from "@/components/HeroMesh";
import { Reveal } from "./Motion";
import { AssistantPreview, EvidenceExample, ExplorerPreview } from "./ProductPreview";
import { ProblemStory, QuestionStory, SolutionStory } from "./Stories";

function Actions() {
  return <div className="landing-actions"><Link className="button button-primary" href="/assistant">Ask the Assistant</Link><Link className="button" href="/standards">Explore standards</Link></div>;
}
const features = [
  { name: "Assistant", title: "Ask about Indian Standards.", text: "Describe your product, explore potential standards, and review the evidence behind the response.", href: "/assistant", cta: "Ask the Assistant", kind: "assistant" },
  { name: "Standards", title: "Find the standard that applies.", text: "Search the catalogue by product or IS number. Inspect scope, amendments and related documents.", href: "/standards", cta: "Explore Standards", kind: "standards" },
  { name: "Sources", title: "Trace every answer back to evidence.", text: "Open available source text and document metadata. Keep the original context within reach.", href: "/sources", cta: "Explore Sources", kind: "sources" },
  { name: "Laboratories", title: "Find testing capabilities that match your requirement.", text: "Search by standard, city and capability, then open the laboratory that can test it.", href: "/laboratories", cta: "Explore Laboratories", kind: "laboratories" },
] as const;
const services = [
  ["Certification", "Understand the route and what it asks of you.", "/services"],
  ["Hallmarking", "Ask about hallmarking and the sources behind it.", "/assistant"],
  ["Testing", "Explore testing requirements and laboratory capabilities.", "/laboratories"],
  ["Consumer information", "Find context for standards, quality and product information.", "/assistant"],
  ["Other BIS services", "Start with a question and follow the relevant sources.", "/assistant"],
];
export default function LandingPage() {
  return <div className="landing-page">
    <div className="landing-scroll-progress" aria-hidden><span /></div>
    <section className="landing-hero relative isolate" aria-labelledby="hero-title"><HeroMesh /><div className="landing-section">
      <Reveal className="hero-copy"><p className="eyebrow">BIS intelligence</p><h1 id="hero-title">Know the standard.<br /><span className="text-muted">Know what comes next.</span></h1><p className="hero-description">AI-powered intelligence for Indian Standards,<br className="hidden sm:block" /> BIS services, certification, testing and laboratories.</p><Actions /></Reveal>
      <Reveal className="hero-interface"><div className="hero-orbit orbit-left" aria-hidden><span className="text-chroma">↳</span> Source document<span className="block mt-2 text-faint">IS reference · Clause 4.2</span></div><AssistantPreview /><div className="hero-orbit orbit-right" aria-hidden><span className="status-dot" /> Testing laboratory<span className="block mt-2 text-faint">Certification route ↗</span></div></Reveal>
      <div className="hero-footnote"><span>Product understanding → Source evidence → Next steps</span><a href="#problem-title">Explore the journey <span aria-hidden>↓</span></a></div>
    </div></section>
    <ProblemStory />
    <SolutionStory />
    <QuestionStory />
    <section className="landing-section evidence-section border-t border-line" aria-labelledby="evidence-title"><Reveal><p className="eyebrow">AI + evidence</p><h2 id="evidence-title" className="landing-heading">Answers are only useful<br /><span className="text-muted">when you can verify them.</span></h2><p className="landing-description">Specengine doesn’t just answer.<br />It shows where the answer came from.</p></Reveal><Reveal><ol className="retrieval-flow">{["Question", "Retrieval", "Relevant BIS sources", "Context", "Answer", "Citation"].map((x, i) => <li key={x}><span className="font-mono text-xs text-chroma">0{i + 1}</span><span>{x}</span>{i < 5 && <span className="retrieval-arrow" aria-hidden>→</span>}</li>)}</ol><div className="evidence-demo"><div><span className="eyebrow">From answer to source</span><h3 className="mt-4 text-xl font-medium">The reference is part of the answer.</h3><p className="preview-copy">Inspect the clause, page and available excerpt.<br />Open the example citation to see the source panel.</p></div><div className="evidence-citation"><EvidenceExample /><p className="mt-3 text-xs text-muted">Illustrative reference · Not a standard recommendation</p></div></div></Reveal></section>
    <section className="landing-section border-t border-line" aria-labelledby="explore-title"><Reveal><p className="eyebrow">Explore the product</p><h2 id="explore-title" className="landing-heading">A connected view.<br /><span className="text-muted">At every level of detail.</span></h2></Reveal><div className="feature-list">{features.map((feature, i) => <Reveal className={`feature-section feature-${i}`} key={feature.name}><div className="feature-copy"><p className="eyebrow">0{i + 1} / {feature.name}</p><h3>{feature.title}</h3><p className="landing-description">{feature.text}</p><Link href={feature.href} className="button mt-6">{feature.cta} <span aria-hidden>→</span></Link></div>{feature.kind === "assistant" ? <AssistantPreview stage={0} compact /> : <ExplorerPreview kind={feature.kind} />}</Reveal>)}</div></section>
    <section className="landing-section border-t border-line" aria-labelledby="services-title"><Reveal><p className="eyebrow">BIS services</p><h2 id="services-title" className="landing-heading">Understand the route.<br /><span className="text-muted">Prepare for what follows.</span></h2><p className="landing-description">Explore service information and prepare your next steps. The prototype does not submit applications or testing requests.</p></Reveal><div className="services-grid">{services.map(([title, text, href], i) => <Reveal key={title}><Link className="panel service-card" href={href}><span className="font-mono text-xs text-chroma">0{i + 1}</span><h3 className="mt-6 text-xl font-medium">{title}</h3><p className="preview-copy">{text}</p><span className="mt-auto pt-6 text-sm" aria-hidden>↗</span></Link></Reveal>)}</div></section>
    <section className="landing-section border-t border-line" aria-labelledby="connected-title"><Reveal><p className="eyebrow">The connected journey</p><h2 id="connected-title" className="landing-heading">One question. Every next step.</h2><p className="landing-description">Move from a product question to verifiable requirements, testing guidance and a suitable laboratory without losing the source context.</p></Reveal><ol className="connected-journey">{[["Ask", "Describe the product.", "/assistant"], ["Understand", "Clarify material and intended use.", "/assistant"], ["Find standard", "Review what may apply.", "/standards"], ["Verify source", "Read the published evidence.", "/sources"], ["Requirements", "Understand scope and obligations.", "/services"], ["Testing", "Identify the required tests.", "/services"], ["Laboratory", "Match capability and location.", "/laboratories"]].map(([title, text, href], i) => <li key={title}><Reveal><span className="journey-node">0{i + 1}</span><h3 className="mt-7 text-xl font-medium">{title}</h3><p className="mt-3 text-sm leading-6 text-muted">{text}</p><Link className="mt-5 inline-block text-sm text-ink" href={href}>Open {title.toLowerCase()}<span className="sr-only">: {text}</span></Link></Reveal></li>)}</ol></section>
    <section className="landing-section final-cta border-t border-line" aria-labelledby="final-title"><Reveal><p className="eyebrow">Specengine BIS</p><h2 id="final-title" className="landing-heading">Start with a question.</h2><p className="landing-description">Describe your product or ask about an Indian Standard.</p><Actions /></Reveal></section>
  </div>;
}
