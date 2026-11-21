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

/** The question and final answer of a share (first question, last answer). */
export function questionAndAnswer(messages: Message[]): { question: string; answer: string } {
  const question = messages.find((m) => m.role === "user" && m.content?.trim())?.content?.trim() ?? "";
  const answer = [...messages].reverse().find((m) => m.role === "assistant" && m.content?.trim())?.content?.trim() ?? "";
  return { question, answer };
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
  return `${question}\n\n${plainText(answer, max)}\n\nSee the full answer: ${url}`;
}
