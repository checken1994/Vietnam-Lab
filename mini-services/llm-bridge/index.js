/**
 * Canonical Node.js ESM entrypoint for SCP LLM Bridge.
 */
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { existsSync } from "node:fs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const distFile = join(__dirname, "dist", "index.js");

if (existsSync(distFile)) {
  await import("./dist/index.js");
} else {
  const { createJiti } = await import(join(__dirname, "..", "..", "dashboard", "node_modules", "jiti", "dist", "jiti.cjs"));
  const jiti = createJiti(__filename);
  await jiti.import("./core.ts");
}
