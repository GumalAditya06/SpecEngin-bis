import type { SVGProps } from "react";

/**
 * Specengine-BIS brand mark, in four layouts. See BRAND.md for usage rules.
 *
 * Rendered as inline SVG (not <img src="/logo-*.svg">) so `currentColor` picks up
 * the surrounding text color — set color with a className like `text-[#F5F6F7]`
 * or a Tailwind `text-*` utility, the same way you'd color an icon.
 */
export type LogoVariant = "mark" | "mark-16" | "lockup" | "lockup-stacked";

export interface LogoProps extends Omit<SVGProps<SVGSVGElement>, "children"> {
  /** Which layout to render. Defaults to the horizontal lockup. */
  variant?: LogoVariant;
  /** Accessible name. Pass `title=""` to mark the mark as purely decorative. */
  title?: string;
}

const VIEWBOX: Record<LogoVariant, string> = {
  mark: "0 0 24 24",
  "mark-16": "0 0 16 16",
  lockup: "0 0 220 32",
  "lockup-stacked": "0 0 160 118",
};

function MarkGlyph() {
  return (
    <g fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="square" strokeLinejoin="miter">
      <rect x={2} y={2} width={20} height={20} rx={3} />
      <path d="M 7.5 7.5 H 16.5 V 11.5 H 13.5 V 16.5 H 7.5 Z" />
    </g>
  );
}

function Mark16Glyph() {
  return (
    <g fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="square" strokeLinejoin="miter">
      <rect x={1} y={1} width={14} height={14} rx={2} />
      <path d="M 5 5 H 11 V 8 H 9 V 11 H 5 Z" />
    </g>
  );
}

/**
 * The "Specengine" wordmark.
 *
 * The original generation drew this as hand-authored bezier letterforms. Several of those
 * paths were corrupted (bad control points) and rendered as mirrored, illegible glyphs —
 * confirmed by rendering `logo-lockup.svg` directly in a browser, not just by reading the
 * path data. Rather than hand-debug ~10 glyphs' worth of curves with no ground truth to check
 * against, this uses real `<text>` — the same approach the "BIS" pill already used correctly
 * in the original file. It will always render legibly, in whatever font is actually loaded.
 */
function Wordmark({ x = 0, textAnchor = "start" }: { x?: number; textAnchor?: "start" | "middle" }) {
  return (
    <text
      x={x}
      y={0}
      textAnchor={textAnchor}
      fontFamily="Inter, -apple-system, sans-serif"
      fontSize={20}
      fontWeight={600}
      letterSpacing="-0.02em"
      fill="currentColor"
    >
      Specengine
    </text>
  );
}

/** The "BIS" pill badge, top-left anchored at (0,0). Shared by both lockups. */
function BisPill() {
  return (
    <g>
      <rect x={0} y={0} width={34} height={18} rx={9} fill="none" stroke="currentColor" strokeOpacity={0.2} strokeWidth={1} />
      <text x={17} y={12.5} fontFamily="Inter, sans-serif" fontSize={11} fontWeight={500} letterSpacing="0.04em" fill="currentColor" fillOpacity={0.6} textAnchor="middle">
        BIS
      </text>
    </g>
  );
}

function LockupContent() {
  return (
    <>
      <g transform="scale(1.3333)">
        <MarkGlyph />
      </g>
      <g transform="translate(0, 22)">
        <Wordmark x={48} />
      </g>
      <g transform="translate(176, 7)">
        <BisPill />
      </g>
    </>
  );
}

function LockupStackedContent() {
  return (
    <>
      <g transform="translate(56, 0) scale(2)">
        <MarkGlyph />
      </g>
      <g transform="translate(0, 72)">
        <Wordmark x={80} textAnchor="middle" />
      </g>
      <g transform="translate(63, 90)">
        <BisPill />
      </g>
    </>
  );
}

export function Logo({ variant = "lockup", title = "Specengine-BIS", className, ...props }: LogoProps) {
  const isIconOnly = variant === "mark" || variant === "mark-16";

  return (
    <svg
      viewBox={VIEWBOX[variant]}
      className={className}
      fill={isIconOnly ? "none" : "currentColor"}
      role="img"
      aria-label={title || undefined}
      aria-hidden={title ? undefined : true}
      {...props}
    >
      {variant === "mark" && <MarkGlyph />}
      {variant === "mark-16" && <Mark16Glyph />}
      {variant === "lockup" && <LockupContent />}
      {variant === "lockup-stacked" && <LockupStackedContent />}
    </svg>
  );
}

export default Logo;
