import { redirect } from "next/navigation";

// Source detail lives at /sources/[id]. Old document links keep working.
export default async function DocumentDetailRedirect({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const { id } = await params;
  const query = await searchParams;
  const search = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value) search.set(key, value);
  });
  const suffix = search.toString();
  redirect(suffix ? `/sources/${id}?${suffix}` : `/sources/${id}`);
}
