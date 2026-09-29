"use client";

export type RecentQuestion = { question: string; timestamp: number };
export const HISTORY_KEY = "specengine.recent-questions.v1";
export function readQuestions(): RecentQuestion[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((x): x is RecentQuestion => !!x && typeof x.question === "string" && x.question.length <= 6000 && typeof x.timestamp === "number" && Number.isFinite(x.timestamp)).slice(0, 12);
  } catch { return []; }
}
export default function RecentQuestions({ questions, onSelect, onClear, disabled }: { questions: RecentQuestion[]; onSelect: (q: string) => void; onClear: () => void; disabled: boolean }) {
  const today = new Date().toDateString();
  const yesterday = new Date(); yesterday.setDate(yesterday.getDate() - 1);
  const groups = ["Today", "Yesterday", "Earlier"];
  const group = (t: number) => new Date(t).toDateString() === today ? "Today" : new Date(t).toDateString() === yesterday.toDateString() ? "Yesterday" : "Earlier";
  return <details className="recent-questions" open={questions.length > 0}><summary>Recent questions <span className="text-muted">{questions.length ? `(${questions.length})` : ""}</span></summary><div className="recent-content">
    {!questions.length && <p className="text-sm text-muted">Your recent questions will appear here.</p>}
    {groups.map(label => { const items = questions.filter(q => group(q.timestamp) === label); return items.length ? <div key={label}><h2 className="mb-3 text-xs text-muted">{label}</h2><ul>{items.map(item => <li key={item.question}><button disabled={disabled} onClick={() => onSelect(item.question)}>{item.question}</button></li>)}</ul></div> : null; })}
    {questions.length > 0 && <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4 text-xs text-muted"><span>Questions saved in this browser.</span><button onClick={onClear} className="underline underline-offset-4">Clear history</button></div>}
  </div></details>;
}
