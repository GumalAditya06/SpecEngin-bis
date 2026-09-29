import SearchEvidence from "./content";

export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q } = await searchParams;
  return <SearchEvidence key={q || "empty"} initialQuery={q || ""} />;
}
