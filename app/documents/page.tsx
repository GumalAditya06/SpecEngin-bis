import { redirect } from "next/navigation";

// The source library is the primary route. `/documents` is kept as a
// compatibility entry point that preserves the catalogue query string.
export default async function DocumentsRedirect({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const query = await searchParams;
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  const search = params.toString();
  redirect(search ? `/sources?${search}` : "/sources");
}
