import { readFile } from "node:fs/promises";
import path from "node:path";

import { zip } from "@/lib/zip";

export const dynamic = "force-dynamic";

const DIR = path.join(process.cwd(), "extension", "collibra-bridge");
const FILES = ["manifest.json", "background.js", "content.js", "policy.js", "options.html", "options.js", "README.md"];
const FOLDER = "easyquery-collibra-bridge";

/** The EasyQuery Collibra Bridge extension as a zip to unpack and "Load unpacked" in Chrome/Edge. */
export async function GET() {
  try {
    const files = await Promise.all(
      FILES.map(async (name) => ({ name: `${FOLDER}/${name}`, data: await readFile(path.join(DIR, name)) })),
    );
    const version = JSON.parse(files[0].data.toString("utf8")).version as string;
    return new Response(new Uint8Array(zip(files)), {
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition": `attachment; filename="${FOLDER}-${version}.zip"`,
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return new Response("Extension package not available", { status: 503 });
  }
}
