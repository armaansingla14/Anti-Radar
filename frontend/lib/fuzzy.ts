/**
 * Small subsequence fuzzy matcher for the command palette.
 * Returns null when `query` is not a subsequence of `text`; higher is better.
 */
export function fuzzyScore(query: string, text: string): number | null {
  const q = query.trim().toLowerCase();
  if (!q) return 0;
  const t = text.toLowerCase();
  if (t === q) return 1000;
  if (t.startsWith(q)) return 500 - t.length;
  const sub = t.indexOf(q);
  if (sub >= 0) return 300 - sub - t.length / 10;

  let score = 0;
  let ti = 0;
  let prev = -2;
  for (const ch of q) {
    if (ch === " ") continue;
    const found = t.indexOf(ch, ti);
    if (found < 0) return null;
    const wordStart = found === 0 || /[\s/_\-.]/.test(t[found - 1]);
    score += 10 + (found === prev + 1 ? 8 : 0) + (wordStart ? 6 : 0) - Math.min(found - ti, 5);
    prev = found;
    ti = found + 1;
  }
  return score - t.length / 10;
}

/** Rank items by best fuzzy score over their searchable fields; drops non-matches. */
export function fuzzyRank<T>(query: string, items: T[], fields: (item: T) => string[]): T[] {
  if (!query.trim()) return items;
  return items
    .map((item, i) => {
      const scores = fields(item)
        .map((f) => fuzzyScore(query, f))
        .filter((s): s is number => s !== null);
      return { item, i, score: scores.length ? Math.max(...scores) : null };
    })
    .filter((x): x is { item: T; i: number; score: number } => x.score !== null)
    .sort((a, b) => b.score - a.score || a.i - b.i)
    .map((x) => x.item);
}
