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
    .replace(/^\s{0,3}#{1,6}\s.*\b(sql|quer(?:y|ies))\b.*$/gim, "")
    .replace(/^.*\b(query executed|queries executed|query was (?:executed|run)|queries were (?:executed|run)|following (?:sql|query|queries)|sql used|workspace table|saved as|source table)\b.*$/gim, "")
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
    .replace(/(^|[\s(])[*_]([^*_\n]+)[*_](?=[\s).,;:!?]|$)/g, "$1$2")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > max ? `${text.slice(0, max).replace(/\s+\S*$/, "")}…` : text;
}

const STRUCTURE = /^\s*(#|\||[-*+]\s|\d+\.\s|-{3,}|\*{3,}|```)/;

/**
 * The answer in one or two sentences, for previews and chat messages: prose only (no tables, lists or headings),
 * cut at a sentence end.
 */
export function highlight(markdown: string, max = 320): string {
  const prose = cleanAnswer(markdown).split(/\n\s*\n/).filter((b) => b.trim() && !STRUCTURE.test(b));
  let text = "";
  for (const block of prose) {
    const next = `${text} ${plainText(block, 2000)}`.trim();
    if (text && next.length > max) break;
    text = next;
  }
  text = text.replace(/:\s*$/, ".");
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const end = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("! "), cut.lastIndexOf("? "));
  return end > max / 3 ? cut.slice(0, end + 1) : `${cut.replace(/\s+\S*$/, "")}…`;
}

/** A starting title for a shared answer: the chart's title, the answer's first heading, or the tidied question. */
export function suggestTitle(question: string, answer: string, chartTitle?: string): string {
  if (chartTitle?.trim()) return chartTitle.trim().slice(0, 120);
  const heading = cleanAnswer(answer).match(/^\s{0,3}#{1,4}\s+(.+)$/m)?.[1];
  if (heading) return plainText(heading, 120);
  const q = question
    .replace(/^\s*(pls|please|could you|can you|would you|show me|give me|tell me)\b[\s,]*/i, "")
    .replace(/[\s,]*(pls|please)?\s*(give me|show me|with)?\s*(a|an)?\s*(table( list)?|bar|line)?\s*(report|chart)[^.?!]*[.?!]?\s*$/i, "")
    .replace(/\s+/g, " ")
    .trim();
  const t = q.charAt(0).toUpperCase() + q.slice(1);
  return t.length > 120 ? `${t.slice(0, 119).replace(/\s+\S*$/, "")}…` : t;
}

/** The message for Slack, Teams, email or Copy summary: title, one or two sentences, link. */
export function summaryText(title: string, answer: string, url: string, bold = ""): string {
  const h = highlight(answer);
  return `${bold}${title}${bold}${h ? `\n${h}` : ""}\n${url}`;
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
