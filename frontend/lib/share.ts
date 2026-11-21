/** Shared-answer helpers used by the share dialog, the /s/[id] page and its link-preview image. */

import type { ChartData } from "@/app/components/Chart";

type Message = { id: string; role: "user" | "assistant" | "tool"; content?: string };
export type PublicArtifact =
  | { kind: "chart"; name: string; title: string; result: ChartData }
  | { kind: "table"; name: string; row_count: number; columns: string[]; rows: Record<string, unknown>[] };
/** An "anyone with the link" share as the API returns it without sign-in. */
export type PublicShare = {
  id: string; kind: "conversation" | "answer"; title: string; messages: Message[]; artifacts: PublicArtifact[];
  created_at: string; expires_at: string; owner_name: string; organization: string;
};

export const SHARE_ID = /^[A-Za-z0-9_-]{22}$/;

/**
 * The answer as a summary report: no SQL/code, no "Query executed (q1)" / "Workspace table: q2" lines and no
 * internal result names (q1, c1) — those mean nothing to someone reading a shared link.
 */
export function cleanAnswer(markdown: string): string {
  const text = markdown
    .replace(/```[\s\S]*?```/g, "")
    .replace(/^.*\b(query executed|queries executed|sql used|workspace table|saved as|source table)\b.*$/gim, "")
    .replace(/\s*\((?:`?[qc]\d+`?(?:\s*(?:,|and)\s*)?)+\)/g, "")
    .replace(/\b(?:in|from|as|see|table|chart|result)\s+`[qc]\d+`/gi, "")
    .replace(/`[qc]\d+`/g, "");
  // Drop separators left with nothing between them, keeping blank lines around the rest (a "---" right under a
  // paragraph would turn it into a heading).
  const blocks: string[] = [];
  for (const block of text.split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean)) {
    const rule = /^(?:-{3,}|\*{3,})$/.test(block);
    if (rule && (!blocks.length || /^(?:-{3,}|\*{3,})$/.test(blocks[blocks.length - 1]))) continue;
    blocks.push(block);
  }
  while (blocks.length && /^(?:-{3,}|\*{3,})$/.test(blocks[blocks.length - 1])) blocks.pop();
  return blocks.join("\n\n");
}

/** The question and final answer of a share (first question, last answer), the answer as a clean summary. */
export function questionAndAnswer(messages: Message[]): { question: string; answer: string } {
  const question = messages.find((m) => m.role === "user" && m.content?.trim())?.content?.trim() ?? "";
  const answer = [...messages].reverse().find((m) => m.role === "assistant" && m.content?.trim())?.content?.trim() ?? "";
  return { question, answer: cleanAnswer(answer) };
}

/** Markdown to short plain text, for previews, email bodies and chat messages. */
export function plainText(markdown: string, max = 300): string {
  const text = markdown
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/^\s*\|?\s*:?-{2,}.*$/gm, " ")
    .replace(/\|/g, " ")
    .replace(/^\s{0,3}(#{1,6}|>|[-*+])\s+/gm, "")
    .replace(/(\*\*|__|`)/g, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > max ? `${text.slice(0, max).replace(/\s+\S*$/, "")}…` : text;
}

/** Question + short answer + link: what Copy summary, Slack and Email put in the message. */
export function summaryText(question: string, answer: string, url: string, max = 700): string {
  return `${question}\n\n${plainText(cleanAnswer(answer), max)}\n\nSee the full answer: ${url}`;
}

/** What a shared-answer page sends to the browser: the clean question/answer only, never the raw transcript. */
export function forReport(share: PublicShare): PublicShare {
  const { question, answer } = questionAndAnswer(share.messages);
  return {
    ...share,
    messages: [
      { id: "q", role: "user", content: question || share.title },
      { id: "a", role: "assistant", content: answer },
    ],
  };
}
