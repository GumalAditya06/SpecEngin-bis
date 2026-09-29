import Link from "next/link";
export default function NotFound() {
  return (
    <section className="page-wrap">
      <div className="panel py-16 text-center">
        <p className="eyebrow">404 · Not found</p>
        <h1 className="mt-4 text-3xl font-medium">
          This page isn’t in the catalogue.
        </h1>
        <p className="mt-4 text-sm text-muted">
          The link may have changed. You can start again from the standards
          explorer.
        </p>
        <Link className="button button-primary mt-6" href="/standards">
          Explore standards →
        </Link>
      </div>
    </section>
  );
}
