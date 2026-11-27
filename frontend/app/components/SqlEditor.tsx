"use client";

import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap, type Completion } from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap, indentWithTab, toggleComment } from "@codemirror/commands";
import { sql, type SQLNamespace } from "@codemirror/lang-sql";
import { bracketMatching, foldGutter, foldKeymap, indentOnInput, syntaxHighlighting } from "@codemirror/language";
import { highlightSelectionMatches, search, searchKeymap } from "@codemirror/search";
import { Compartment, EditorState } from "@codemirror/state";
import {
  crosshairCursor,
  drawSelection,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
  placeholder as placeholderExt,
  rectangularSelection,
} from "@codemirror/view";
import { useEffect, useLayoutEffect, useRef } from "react";

import { DIALECTS, sqlHighlighter, type SqlDialectId } from "@/lib/sqlSyntax";

export { DIALECT_LABEL, type SqlDialectId } from "@/lib/sqlSyntax";

export type SqlSchema = { tables: { path: string[]; columns: { name: string; type: string }[] }[] };

/** Nested namespace (project → dataset → table → columns) so `proj.ds.` completes tables and `t.` completes columns. */
function namespace(schema: SqlSchema | undefined): { ns: SQLNamespace; defaultTable?: string } {
  const root: Record<string, SQLNamespace> = {};
  for (const table of schema?.tables ?? []) {
    let level = root;
    table.path.slice(0, -1).forEach((part) => {
      level[part] = (level[part] as Record<string, SQLNamespace>) ?? {};
      level = level[part] as Record<string, SQLNamespace>;
    });
    level[table.path[table.path.length - 1]] = table.columns.map(
      (c): Completion => ({ label: c.name, type: "property", detail: c.type.toLowerCase(), boost: 1 }),
    );
  }
  // lang-sql resolves `defaultTable` as one top-level name, so also register the opened table under its short name:
  // its columns then complete unqualified (`SELECT clip_c…`), while `proj.ds.` still walks the nested levels.
  const first = schema?.tables.find((tbl) => tbl.columns.length > 0);
  if (!first) return { ns: root };
  const short = first.path[first.path.length - 1];
  if (!(short in root)) root[short] = first.columns.map((c): Completion => ({ label: c.name, type: "property", detail: c.type.toLowerCase(), boost: 1 }));
  return { ns: root, defaultTable: short };
}

const theme = EditorView.theme(
  {
    "&": { backgroundColor: "var(--code-bg)", color: "var(--code-fg)", fontSize: "13px", height: "100%" },
    "&.cm-focused": { outline: "none" },
    ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.55", overflow: "auto" },
    ".cm-content": { caretColor: "var(--code-cursor)", padding: "10px 0" },
    ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--code-cursor)", borderLeftWidth: "2px" },
    "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, ::selection": {
      backgroundColor: "var(--code-selection) !important",
    },
    ".cm-activeLine": { backgroundColor: "var(--code-active-line)" },
    ".cm-selectionMatch": { backgroundColor: "var(--code-match)" },
    ".cm-matchingBracket, &.cm-focused .cm-matchingBracket": { backgroundColor: "var(--code-match)", outline: "1px solid var(--code-gutter-fg)" },
    ".cm-activeLineGutter": { backgroundColor: "var(--code-active-line)", color: "var(--code-fg)" },
    ".cm-lineNumbers .cm-gutterElement": { padding: "0 10px 0 14px", minWidth: "36px" },
    ".cm-foldPlaceholder": { backgroundColor: "var(--code-toolbar-bg)", border: "1px solid var(--code-border)", color: "var(--code-fg)" },
    ".cm-placeholder": { color: "var(--code-gutter-fg)" },
    ".cm-tooltip": { backgroundColor: "var(--code-toolbar-bg)", color: "var(--code-fg)", border: "1px solid var(--code-border)" },
    ".cm-tooltip-autocomplete > ul > li[aria-selected]": { backgroundColor: "var(--code-selection)", color: "var(--code-fg)" },
    ".cm-completionDetail": { color: "var(--code-gutter-fg)", fontStyle: "normal", marginLeft: "1em" },
    ".cm-panels": { backgroundColor: "var(--code-toolbar-bg)", color: "var(--code-fg)", borderColor: "var(--code-border)" },
    ".cm-panels input, .cm-panels button": { color: "var(--code-fg)", backgroundColor: "var(--code-bg)", border: "1px solid var(--code-border)" },
    ".cm-searchMatch": { backgroundColor: "var(--code-search)" },
    ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: "var(--code-search-current)" },
    ".cm-gutters": { backgroundColor: "var(--code-gutter-bg)", color: "var(--code-gutter-fg)", borderRight: "1px solid var(--code-border)" },
    ".cm-tooltip-autocomplete": { boxShadow: "0 6px 20px rgba(30, 20, 5, 0.14)" },
  },
);

export type EditorStatus = { line: number; col: number; lines: number; chars: number; selection: string };

/**
 * IDE-style SQL editor (CodeMirror 6): dialect-aware highlighting and completion (keywords, tables in scope and their
 * columns), brackets, folding, search (⌘F), multi-cursor, ⌘/ comments. ⌘/Ctrl+Enter runs (the selection if any);
 * Shift+Alt+F formats. Controlled: the parent owns the text and the Run/Format actions.
 */
export default function SqlEditor({
  value,
  onChange,
  onRun,
  onFormat,
  onStatus,
  dialect,
  schema,
  wrap = false,
  insert,
  id,
  ariaLabel = "SQL editor",
  describedBy,
}: {
  value: string;
  onChange: (value: string) => void;
  onRun: (selection: string) => void;
  onFormat?: () => void;
  onStatus?: (status: EditorStatus) => void;
  dialect: SqlDialectId;
  schema?: SqlSchema;
  wrap?: boolean;
  /** Insert text at the cursor (replacing the selection); a new nonce inserts again. */
  insert?: { text: string; nonce: number } | null;
  id?: string;
  ariaLabel?: string;
  describedBy?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const language = useRef(new Compartment());
  const wrapping = useRef(new Compartment());
  // Latest callbacks without recreating the editor.
  const callbacks = useRef({ onChange, onRun, onFormat, onStatus });
  useLayoutEffect(() => {
    callbacks.current = { onChange, onRun, onFormat, onStatus };
  });

  useEffect(() => {
    if (!host.current) return;
    const status = (v: EditorView) => {
      const s = v.state;
      const head = s.selection.main.head;
      const line = s.doc.lineAt(head);
      const selection = s.selection.ranges.map((r) => s.sliceDoc(r.from, r.to)).join("\n");
      callbacks.current.onStatus?.({ line: line.number, col: head - line.from + 1, lines: s.doc.lines, chars: s.doc.length, selection });
    };
    const { ns, defaultTable } = namespace(schema);
    const editor = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          lineNumbers(),
          highlightActiveLineGutter(),
          foldGutter(),
          history(),
          drawSelection(),
          EditorState.allowMultipleSelections.of(true),
          indentOnInput(),
          bracketMatching(),
          closeBrackets(),
          autocompletion({ activateOnTyping: true, icons: false }),
          rectangularSelection(),
          crosshairCursor(),
          highlightActiveLine(),
          highlightSelectionMatches(),
          search({ top: true }),
          syntaxHighlighting(sqlHighlighter),
          theme,
          placeholderExt("Write SQL… (⌘/Ctrl+Enter to run, Ctrl+Space for suggestions)"),
          language.current.of(sql({ dialect: DIALECTS[dialect], schema: ns, defaultTable, upperCaseKeywords: true })),
          wrapping.current.of(wrap ? EditorView.lineWrapping : []),
          keymap.of([
            {
              key: "Mod-Enter",
              preventDefault: true,
              run: (v) => {
                const sel = v.state.selection.main;
                callbacks.current.onRun(sel.empty ? "" : v.state.sliceDoc(sel.from, sel.to));
                return true;
              },
            },
            { key: "Shift-Alt-f", preventDefault: true, run: () => (callbacks.current.onFormat?.(), true) },
            { key: "Mod-/", run: toggleComment },
            ...closeBracketsKeymap,
            ...defaultKeymap,
            ...searchKeymap,
            ...historyKeymap,
            ...foldKeymap,
            ...completionKeymap,
            // Tab indents; Esc then Tab moves focus out of the editor (keyboard users aren't trapped).
            indentWithTab,
          ]),
          EditorView.contentAttributes.of({
            "aria-label": ariaLabel,
            ...(id ? { id } : {}),
            ...(describedBy ? { "aria-describedby": describedBy } : {}),
            spellcheck: "false",
          }),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) callbacks.current.onChange(u.state.doc.toString());
            if (u.docChanged || u.selectionSet) status(u.view);
          }),
        ],
      }),
    });
    view.current = editor;
    status(editor);
    return () => {
      editor.destroy();
      view.current = null;
    };
    // The editor is created once; value, dialect, schema and wrap are synced by the effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Text replaced from outside (opened from chat, Preview data, Format): swap the document, keep undo history.
  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
  }, [value]);

  useEffect(() => {
    const { ns, defaultTable } = namespace(schema);
    view.current?.dispatch({
      effects: language.current.reconfigure(sql({ dialect: DIALECTS[dialect], schema: ns, defaultTable, upperCaseKeywords: true })),
    });
  }, [dialect, schema]);

  useEffect(() => {
    const v = view.current;
    if (!v || !insert) return;
    v.dispatch(v.state.replaceSelection(insert.text), { scrollIntoView: true });
    v.focus();
  }, [insert]);

  useEffect(() => {
    view.current?.dispatch({ effects: wrapping.current.reconfigure(wrap ? EditorView.lineWrapping : []) });
  }, [wrap]);

  return <div ref={host} className="sqled-editor" />;
}
