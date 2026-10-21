"use client";

import { useRef } from "react";
import { addWatch, removeWatch } from "@/lib/api";
import { WatchButton } from "@/components/WatchButton";
import { useStaticSnapshot } from "@/components/StaticSnapshot";

export interface WatchTopicButtonProps {
  topicKey: string;
  /** Watchlist item id when the topic is already watched. */
  itemId: string | null;
  className?: string;
}

/** Watch a thesis subcategory (POST /api/watchlist {entity_type:"topic", topic_key}). */
export function WatchTopicButton({ topicKey, itemId, className }: WatchTopicButtonProps) {
  const staticSnapshot = useStaticSnapshot();
  const item = useRef<string | null>(itemId);
  if (staticSnapshot) return null; // nothing to write to in the verified snapshot
  return (
    <WatchButton
      entityType="topic"
      entityId={topicKey}
      watched={itemId !== null}
      itemId={itemId}
      className={className}
      onToggle={async (next) => {
        if (next) {
          const created = await addWatch({ entity_type: "topic", topic_key: topicKey });
          item.current = created.id;
        } else {
          if (!item.current) throw new Error("No watchlist item to remove");
          await removeWatch(item.current);
          item.current = null;
        }
      }}
    />
  );
}
