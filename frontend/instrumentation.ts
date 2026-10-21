/** Runs once per server instance: installs the verified-snapshot data hook (see lib/snapshot-server.ts). */
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    (await import("./lib/snapshot-server")).install();
  }
}
