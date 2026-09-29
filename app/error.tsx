"use client";
import Link from "next/link";
export default function ErrorPage({
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <section className="page-wrap">
      <div className="panel py-16 text-center">
        <p className="eyebrow">Unable to load this page</p>
        <h1 className="mt-4 text-3xl font-medium">Let’s try that again.</h1>
        <p className="mt-4 text-sm text-muted">
          The requested information could not be loaded. Retry, or start again
          from the assistant.
        </p>
        <div className="mt-6 flex justify-center gap-3">
          <button className="button button-primary" onClick={retry}>
            Retry
          </button>
          <Link href="/assistant" className="button">
            Ask the assistant
          </Link>
        </div>
      </div>
    </section>
  );
}
