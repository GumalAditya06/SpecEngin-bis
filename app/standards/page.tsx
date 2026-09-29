import Catalogue from "@/components/Catalogue";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const query = await searchParams;
  return (
    <Catalogue key={JSON.stringify(query)} kind="standards" query={query} />
  );
}
