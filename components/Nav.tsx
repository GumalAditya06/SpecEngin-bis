"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { DEMO_MODE } from "@/lib/api/config";
import { Logo } from "./Logo";

const LINKS = [
  { href: "/assistant", label: "Ask" },
  { href: "/standards", label: "Standards" },
  { href: "/sources", label: "Sources" },
  { href: "/services", label: "Guidance" },
  { href: "/laboratories", label: "Laboratories" },
];

export default function Nav() {
  const pathname = usePathname();
  return <NavContent key={pathname} pathname={pathname} />;
}

function NavContent({ pathname }: { pathname: string }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  const isActive = (href: string) =>
    pathname === href || pathname.startsWith(`${href}/`);

  return (
    <header className="sticky top-0 z-50 border-b border-line bg-page">
      <div className="mx-auto flex h-16 max-w-[1200px] items-center justify-between px-6 md:px-8">
        <Link
          href="/"
          className="flex items-center rounded-sm focus-visible:ring-2 focus-visible:ring-white/40 focus-visible:outline-none"
          aria-label="Specengine-BIS home"
        >
          <Logo variant="lockup" className="h-7 text-ink" title="" />
        </Link>

        <nav
          aria-label="Primary"
          className="hidden items-center gap-6 text-[0.8125rem] text-muted lg:flex"
        >
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              aria-current={isActive(l.href) ? "page" : undefined}
              className={`nav-link hover:text-ink focus-visible:ring-2 focus-visible:ring-white/40 focus-visible:outline-none ${
                isActive(l.href) ? "text-ink" : ""
              }`}
            >
              {l.label}
            </Link>
          ))}
          <span className="rounded-full border border-line px-3 py-1 text-faint">
            {DEMO_MODE ? "Demo" : "Live"}
          </span>
        </nav>

        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="mobile-nav"
          className="rounded-full border border-line px-4 py-1.5 text-[0.8125rem] text-muted transition-colors hover:border-line-strong hover:text-ink lg:hidden focus-visible:ring-2 focus-visible:ring-white/40 focus-visible:outline-none"
        >
          Menu
        </button>
      </div>

      {open && (
        <nav
          id="mobile-nav"
          aria-label="Primary mobile"
          className="border-t border-line bg-page lg:hidden"
        >
          <ul className="mx-auto max-w-[1200px] space-y-1 px-6 py-4">
            {LINKS.map((l) => (
              <li key={l.href}>
                <Link
                  href={l.href}
                  aria-current={isActive(l.href) ? "page" : undefined}
                  className={`mobile-nav-link block rounded-[var(--r-inner)] px-3 py-2 text-[0.9375rem] hover:bg-surface hover:text-ink ${
                    isActive(l.href) ? "text-ink" : "text-muted"
                  }`}
                >
                  {l.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      )}
    </header>
  );
}
