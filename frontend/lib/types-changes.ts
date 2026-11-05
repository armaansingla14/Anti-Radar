/**
 * SINCE LAST SNAPSHOT — response types for GET /api/changes and GET /api/changes/points.
 * Mirrors `backend/radar/schemas/changes.py` (ChangesReport, SnapshotPoints). Kept apart from
 * `lib/types.ts` (which already has a home-feed `ChangeItem`), so names here carry a `Report` prefix
 * where they would otherwise clash.
 *
 * "New" always means new to Radar (first observed in the later capture), not newly created.
 */
import type { EntityRef, ISODate, ISODateTime } from "./types.ts";

export type FormationChangeStatus = "NEW" | "DROPPED" | "STRENGTHENED" | "WEAKENED" | "UNCHANGED";

/** One thing first observed in the later capture (backend `ChangeItem`). */
export interface ReportChangeItem {
  /** company | person | repository | paper | job | signal */
  entity_type: string;
  id: string;
  slug: string | null;
  name: string;
  /** Source URL (GitHub, arXiv, job posting, signal source). */
  url: string | null;
  /** When the thing itself was created / published / posted, where the source records it. */
  created_at: ISODateTime | null;
  /** When Radar first observed it. */
  first_seen_at: ISODateTime | null;
  /** Current-day Anti / Founder score when there is one. */
  score: number | null;
  detail: string | null;
  /** The company of a job / repository; the entity a signal is about. */
  parent: EntityRef | null;
}

export interface ChangeSection {
  count: number;
  /** Top 25, deterministic order. */
  items: ReportChangeItem[];
}

export interface FamilySignals {
  family: string;
  label: string;
  count: number;
  /** Newest 10. */
  items: ReportChangeItem[];
}

export interface ScoreChange {
  entity_type: string;
  id: string;
  slug: string | null;
  name: string;
  base_score: number;
  current_score: number;
  delta: number;
  components_delta: Record<string, number>;
}

export interface ScoreChangeList {
  count: number;
  items: ScoreChange[];
}

export interface ScoreChanges {
  comparable: boolean;
  reason: string | null;
  base_version: string | null;
  current_version: string | null;
  /** |delta| at or above this is material. */
  threshold: number;
  /** Live on both days, same version, re-observed in between. */
  compared: number;
  /** Compared, |delta| below the threshold. */
  unchanged: number;
  /** Live on both days but nothing observed since the base day: not compared. */
  not_reobserved: number;
  /** Reconstructed or different-version score on one of the days. */
  not_comparable: number;
  /** Scored on the current day only (new to Radar). */
  no_baseline: number;
  accelerated: ScoreChangeList;
  weakened: ScoreChangeList;
}

export interface CollisionChange {
  entity_type: string;
  id: string;
  slug: string | null;
  name: string;
  /** null: no score on the base day. */
  base_strength: string | null;
  current_strength: string | null;
  families: string[];
  new_entity: boolean;
}

export interface CollisionChanges {
  count: number;
  items: CollisionChange[];
}

export interface FormationChange {
  anchor_id: string;
  anchor_type: string;
  slug: string;
  title: string;
  kind: string;
  status: FormationChangeStatus;
  base_score: number | null;
  current_score: number | null;
  base_tier: string | null;
  current_tier: string | null;
  /** The company/person slug of the anchor (links work for DROPPED candidates). */
  anchor_slug?: string | null;
  /** DROPPED: why it no longer qualifies. */
  reason: string | null;
}

export interface FormationChanges {
  available: boolean;
  reason: string | null;
  base_day: ISODate | null;
  current_day: ISODate | null;
  /** NEW / DROPPED / STRENGTHENED / WEAKENED / UNCHANGED */
  counts: Partial<Record<FormationChangeStatus, number>>;
  /** Formation Score points for STRENGTHENED / WEAKENED within a tier. */
  threshold?: number;
  new: FormationChange[];
  dropped: FormationChange[];
  strengthened: FormationChange[];
  weakened: FormationChange[];
}

export interface MeetMove {
  person: EntityRef;
  base_rank: number | null;
  current_rank: number | null;
  /** Positive: moved up (base_rank − current_rank). */
  change: number | null;
}

export interface MeetChanges {
  available: boolean;
  reason: string | null;
  base_week: string | null;
  current_week: string | null;
  entered: MeetMove[];
  left: MeetMove[];
  /** In both weeks, rank changed. */
  moved: MeetMove[];
}

export interface ChangesReport {
  available: boolean;
  reason: string | null;
  base_day: ISODate | null;
  current_day: ISODate | null;
  /** Every snapshot day replayed into this database. */
  points: ISODate[];
  /** False when the current side is the live database. */
  is_snapshot: boolean;
  new_companies: ChangeSection;
  new_people: ChangeSection;
  new_repositories: ChangeSection;
  new_papers: ChangeSection;
  /** New hiring. */
  new_jobs: ChangeSection;
  /** New papers by people already on Radar. */
  new_research: ChangeSection;
  new_signals_total: number;
  new_signals_by_family: FamilySignals[];
  score_changes: ScoreChanges;
  momentum_acceleration: ScoreChangeList;
  new_collisions: CollisionChanges;
  collisions_ended: CollisionChanges;
  formation: FormationChanges;
  meet: MeetChanges;
  caveats: string[];
}

export interface SnapshotPoint {
  as_of: ISODate;
  /** base | update */
  kind: string | null;
  link: number | null;
  files: number | null;
  captured_at: ISODateTime | null;
}

export interface SnapshotPoints {
  points: SnapshotPoint[];
  latest: ISODate | null;
  default_base: ISODate | null;
  default_current: ISODate | null;
  is_snapshot: boolean;
}
