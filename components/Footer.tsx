import Link from "next/link";
import { Logo } from "./Logo";
import { DEMO_MODE } from "@/lib/api/config";
export default function Footer() {
  return (
    <footer className="mt-24 border-t border-line">
      <div className="mx-auto flex max-w-[1200px] flex-col justify-between gap-8 px-6 py-10 md:flex-row md:px-8">
        <div>
          <Logo
            variant="lockup"
            className="h-6 text-ink"
            title="Specengine-BIS"
          />
          <p className="mt-3 max-w-sm text-xs leading-6 text-muted">
            From identifying a standard to tracing its evidence and finding who
            can test against it.
          </p>
        </div>
        <nav
          aria-label="Footer"
          className="flex flex-wrap gap-6 text-sm text-muted"
        >
          <Link href="/assistant">Ask</Link>
          <Link href="/standards">Standards</Link>
          <Link href="/sources">Sources</Link>
          <Link href="/services">Guidance</Link>
          <Link href="/laboratories">Laboratories</Link>
        </nav>
      </div>
      <p className="mx-auto max-w-[1200px] px-6 pb-8 text-xs text-faint md:px-8">
        © 2026 Specengine-BIS ·{" "}
        {DEMO_MODE
          ? "Sample data for demonstration. No applications or laboratory requests are submitted."
          : "Verify applicability against the original source."}
      </p>
    </footer>
  );
}
