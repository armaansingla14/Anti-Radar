"use client";

import { useRef } from "react";
import { addWatch, removeWatch } from "@/lib/api";
import { WatchButton } from "@/components/WatchButton";

export interface WatchTopicButtonProps {
  topicKey: string;
  /** Watchlist item id when the topic is already watched. */
  itemId: string | null;
  className?: string;
}

/** Watch a thesis subcategory (POST /api/watchlist {entity_type:"topic", topic_key}). */
export function WatchTopicButton({ topicKey, itemId, className }: WatchTopicButtonProps) {
  const item = useRef<string | null>(itemId);
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
