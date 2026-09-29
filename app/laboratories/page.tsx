import LaboratoryCatalogue from "@/components/laboratories/LaboratoryCatalogue";

export const metadata = {
  title: "Laboratories — Specengine BIS",
  description:
    "Find a BIS-recognized testing laboratory by city, recognition status, testing capability and standard.",
};

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const query = await searchParams;
  // The data hook fetches on mount, so a filter change remounts the directory
  // with the new parameters — the same contract the standards catalogue uses.
  return <LaboratoryCatalogue key={JSON.stringify(query)} query={query} />;
}
