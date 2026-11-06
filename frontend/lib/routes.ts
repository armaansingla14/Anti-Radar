/** Navigation map shared by the masthead, command palette and G-shortcuts. */
export interface NavItem {
  href: string;
  label: string;
  /** Shorter label for the masthead strip when space is tight (the full label stays in the tooltip and palette). */
  short?: string;
  /** Second key of the "G then …" jump sequence, if any. */
  jump?: string;
  description: string;
}

export const NAV_PRIMARY: NavItem[] = [
  { href: "/", label: "Radar", jump: "r", description: "Today's ranked intelligence feed" },
  { href: "/formation", label: "Formation", jump: "o", description: "Technical teams before the company announcement" },
  { href: "/meet", label: "Meet", jump: "m", description: "Who to meet this week" },
  { href: "/diligence", label: "Diligence", jump: "i", description: "What a company claims, the evidence, and what remains unknown" },
  { href: "/portfolio", label: "Portfolio", jump: "p", description: "What changed after we cared?" },
  { href: "/agents", label: "Agents", jump: "a", description: "How reliable the research agents are: metrics, failures, regression cases" },
  { href: "/founders", label: "Founders", jump: "f", description: "Technical founders surfacing now" },
  { href: "/companies", label: "Companies", jump: "c", description: "Emerging companies and projects" },
  { href: "/thesis", label: "Thesis", jump: "t", description: "Momentum versus attention by thesis area" },
  { href: "/watchlist", label: "Watchlist", jump: "w", description: "Entities you are tracking" },
  { href: "/search", label: "Search", jump: "s", description: "Natural-language search over the radar" },
];

export const NAV_SECONDARY: NavItem[] = [
  { href: "/daily", label: "Daily", jump: "d", description: "What changed since your last visit" },
  { href: "/brief", label: "Brief", jump: "b", description: "Morning brief" },
  { href: "/retrospective", label: "Retrospective", short: "Retro", description: "How the framework reads known breakouts" },
  { href: "/status", label: "Status", description: "Providers, freshness, LLM usage" },
];

export const ALL_NAV: NavItem[] = [...NAV_PRIMARY, ...NAV_SECONDARY];

export function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}
