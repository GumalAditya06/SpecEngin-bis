import { getDocument } from "@/lib/api/client";
import { loadDetail } from "@/lib/api/detail";
import SourceContent from "./content";

export const metadata = {
  title: "Source — Specengine BIS",
  description:
    "Source metadata, clause structure and the text available under its licence.",
};

export default async function SourceDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const { id } = await params;
  const { clause } = await searchParams;
  const doc = await loadDetail(() => getDocument(id));

  return <SourceContent doc={doc} initialClause={clause || null} />;
}
