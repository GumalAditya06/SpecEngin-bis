import { Skeletons } from "@/components/ErrorState";
export default function Loading() {
  return (
    <div className="page-wrap" role="status">
      <span className="sr-only">Loading page…</span>
      <Skeletons />
    </div>
  );
}
