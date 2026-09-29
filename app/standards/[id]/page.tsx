import { getDocument, getStandard } from "@/lib/api/client";
import { loadDetail } from "@/lib/api/detail";
import StandardsDetail from "./content";
export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const standard = await loadDetail(() => getStandard(id));
  const details = await Promise.allSettled(
    standard.documents.map((document) => getDocument(document.id)),
  );
  const documentDetails = details.flatMap((result) =>
    result.status === "fulfilled" ? [result.value] : [],
  );
  return (
    <StandardsDetail
      standard={standard}
      documents={standard.documents}
      documentDetails={documentDetails}
      documentsTotal={standard.document_count}
      amendments={standard.amendments}
      related={standard.related}
      laboratories={standard.laboratories}
    />
  );
}
