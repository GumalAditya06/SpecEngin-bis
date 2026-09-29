import Link from "next/link";

export function UnavailablePage({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <section className="mx-auto max-w-[1200px] px-6 pt-24 md:px-8 md:pt-32">
      <div className="rounded-[var(--r-card)] border border-line bg-surface p-12 text-center">
        <h2 className="text-[1.25rem] font-medium tracking-tight text-ink">
          {title}
        </h2>
        <p className="mx-auto mt-3 max-w-[52ch] text-base leading-7 text-muted">
          {description}
        </p>
        <Link
          href="/"
          className="mt-6 inline-flex h-10 items-center rounded-full bg-accent px-7 text-[0.9375rem] font-medium text-accent-fg transition-opacity hover:opacity-90"
        >
          Return to home
        </Link>
      </div>
    </section>
  );
}
