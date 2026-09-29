import { notFound } from "next/navigation";
import { ApiError } from "./errors";
// Server-only usage: preserve a useful 404 while letting outages reach error.tsx.
export async function loadDetail<T>(load: () => Promise<T>): Promise<T> {
  try {
    return await load();
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }
}
