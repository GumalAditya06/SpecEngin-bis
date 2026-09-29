import Catalogue from "@/components/Catalogue";

export const metadata = {
  title: "Sources — Specengine BIS",
  description:
    "Read the standards, schemes and official documents behind your answers.",
};

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const query = await searchParams;
  return <Catalogue key={JSON.stringify(query)} kind="sources" query={query} />;
}
