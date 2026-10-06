"use client";

import { useSyncExternalStore } from "react";

const subscribe = () => () => {};

/** True only on the client after hydration — gate portals and browser-only UI. */
export function useMounted(): boolean {
  return useSyncExternalStore(subscribe, () => true, () => false);
}
