import { getLaboratory } from "@/lib/api/client";
import { loadDetail } from "@/lib/api/detail";
import { toLaboratoryRecord } from "@/lib/laboratories/records";
import { loadSourcesForStandards } from "@/lib/sources/load";
import LaboratoryDetail from "./content";

export const metadata = {
  title: "Laboratory — Specengine BIS",
  description:
    "Recognition, testing capabilities and the standards a BIS-recognized laboratory can test.",
};

export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const data = await loadDetail(() => getLaboratory(id));
  const laboratory = toLaboratoryRecord(data, data);
  // Sources for the lab's associated standards, through the existing standard
  // endpoint — a partial failure still renders the laboratory page.
  const sources = await loadSourcesForStandards(
    data.standards.map((standard) => standard.id),
  );

  return <LaboratoryDetail laboratory={laboratory} sources={sources} />;
}
