"use client";

import { Logo } from "@/components/Logo";
import { AssistantPreview, sampleQuestion } from "./ProductPreview";
import { Reveal, useScrollStory } from "./Motion";

const fragments = ["Indian Standards", "Source PDFs", "Certification schemes", "Testing requirements", "Laboratory information", "BIS services"];
export function ProblemStory() {
  const { ref, step } = useScrollStory(3);
  return <section className="landing-section border-t border-line" aria-labelledby="problem-title">
    <Reveal><p className="eyebrow">The problem</p><h2 id="problem-title" className="landing-heading">Indian Standards are everywhere.<br /><span className="text-muted">Finding what actually applies<br className="hidden md:block" /> is the hard part.</span></h2><p className="landing-description">A product question can lead to a standard, a PDF, a certification scheme, a test method, and a laboratory list. The challenge is connecting them.</p></Reveal>
    <div ref={ref} className="fragment-scroll">
      <div className={`fragment-scene fragment-state-${step}`}>
        <div className="fragment-caption"><span className="eyebrow">{["01 / Search across sources", "02 / Make sense of the fragments", "03 / Bring the context together"][step]}</span><span className="text-xs text-muted hidden lg:block">Scroll to connect</span></div>
        <div className="fragment-field">{fragments.map((text, i) => <div key={text} className={`document-fragment fragment-${i}`}><span className="font-mono text-xs text-chroma">0{i + 1} / SOURCE</span><p className="mt-4 text-sm">{text}</p><div className="fragment-lines" aria-hidden><i /><i /></div></div>)}</div>
        <div className="fragment-resolution"><Logo variant="mark" title="" className="h-6 w-6" /><div><p>Specengine BIS</p><p className="mt-1 text-sm text-muted">One question. Connected context.</p></div><span aria-hidden className="ml-auto text-chroma">↗</span></div>
      </div>
    </div>
  </section>;
}

const workflow = ["Query", "Relevant standard", "Requirements", "Certification", "Testing", "Laboratory", "Sources"];
export function SolutionStory() {
  const { ref, step } = useScrollStory(workflow.length);
  return <section className="landing-section border-t border-line" aria-labelledby="solution-title"><Reveal><p className="eyebrow">One intelligent interface</p><h2 id="solution-title" className="landing-heading">Ask once.<br /><span className="text-muted">Trace everything.</span></h2></Reveal>
    <div className="solution-scroll" ref={ref}><div className="solution-pin"><div className="solution-question"><Logo variant="mark" title="" className="h-6 w-6 shrink-0" /><p><span className="typed-query">{sampleQuestion}</span></p></div><ol className="workflow-list">{workflow.map((label, i) => <li key={label} className={i <= step ? "workflow-active" : ""}><span className="workflow-number">0{i + 1}</span><span>{label}</span><span className="workflow-connector" aria-hidden>↓</span></li>)}</ol><p className="mt-7 text-xs text-muted">A guided workflow. Applicability is checked against the source.</p></div></div>
  </section>;
}

const states = [
  ["Describe your product.", "Start in your own words. Material, construction and intended use give the assistant a useful starting point."],
  ["Find what applies.", "Review candidate Indian Standards and their scope. Understand why a result may be relevant."],
  ["Understand the requirements.", "Bring product requirements, testing and marking into focus before planning the work."],
  ["Verify the evidence.", "Follow a citation into the source. Read the clause and its context, rather than relying on an answer alone."],
  ["Find the next step.", "Explore testing capabilities and the laboratories that hold them, then ask the assistant about the route that follows."],
];
export function QuestionStory() {
  const { ref, step } = useScrollStory(states.length);
  return <section className="landing-section border-t border-line" aria-labelledby="question-title"><p className="eyebrow">From question to answer</p><h2 id="question-title" className="sr-only">A guided product journey</h2>
    <div ref={ref} className="question-scroll"><div className="question-pin"><div className="question-copy" key={step}><span className="font-mono text-sm text-chroma">0{step + 1} / 05</span><h3 className="landing-heading">{states[step][0]}</h3><p className="landing-description">{states[step][1]}</p><div className="story-track" aria-hidden><span /></div><p className="mt-5 text-xs text-muted">Scroll to follow the question</p></div><AssistantPreview stage={step} compact /></div></div>
    <div className="question-sequential">{states.map(([title, text], i) => <Reveal key={title} className="sequential-step"><span className="font-mono text-xs text-chroma">0{i + 1} / 05</span><h3 className="mt-5 text-3xl font-medium tracking-tight">{title}</h3><p className="landing-description">{text}</p><AssistantPreview stage={i} compact /></Reveal>)}</div>
  </section>;
}
