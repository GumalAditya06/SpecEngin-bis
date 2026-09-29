export function resolveDemoMode(value: string | undefined): boolean {
  return value === "true";
}

// Live API mode is the safe default. Demo data requires an explicit opt-in.
export const DEMO_MODE = resolveDemoMode(process.env.NEXT_PUBLIC_DEMO_MODE);
