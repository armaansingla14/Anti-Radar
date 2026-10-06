import "server-only";
import { getThesis } from "@/lib/api";
import type { SelectOption } from "./IndexControls";

/** Theme + topic options from the thesis taxonomy (empty list if unavailable). */
export async function themeOptions(): Promise<SelectOption[]> {
  try {
    const map = await getThesis();
    return map.themes.flatMap((t) => [
      { value: t.key, label: `All ${t.label}`, group: t.label },
      ...t.cells.map((c) => ({ value: c.key, label: c.label, group: t.label })),
    ]);
  } catch {
    return [];
  }
}
