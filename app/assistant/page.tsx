import Assistant from "./content";
export const metadata = { title: "Assistant — Specengine BIS", description: "Ask about Indian Standards, requirements, BIS services and source evidence." };

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q } = await searchParams;
  return <Assistant key={q || ""} initialQuery={q || ""} />;
}
