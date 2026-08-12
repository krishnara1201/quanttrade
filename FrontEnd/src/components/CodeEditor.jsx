import React, { useEffect, useRef } from 'react';
import { EditorState } from '@codemirror/state';
import { EditorView, keymap, lineNumbers, highlightActiveLine } from '@codemirror/view';
import {
  bracketMatching,
  indentOnInput,
  syntaxHighlighting,
  HighlightStyle,
} from '@codemirror/language';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import {
  closeBrackets,
  closeBracketsKeymap,
  autocompletion,
  completeFromList,
} from '@codemirror/autocomplete';
import { linter, lintGutter } from '@codemirror/lint';
import { python } from '@codemirror/lang-python';
import { tags as t } from '@lezer/highlight';
import { validateCode } from '../api/strategies.js';

const COMPLETIONS = [
  { label: 'df', type: 'variable', info: 'The OHLCV DataFrame passed to generate_signals' },
  { label: 'pd', type: 'module', info: 'pandas' },
  { label: 'np', type: 'module', info: 'numpy' },
  { label: 'generate_signals', type: 'function', info: 'Required entry point: (df) -> pd.Series' },
  { label: 'open', type: 'property' },
  { label: 'high', type: 'property' },
  { label: 'low', type: 'property' },
  { label: 'close', type: 'property' },
  { label: 'volume', type: 'property' },
  { label: 'rolling', type: 'method' },
  { label: 'mean', type: 'method' },
  { label: 'std', type: 'method' },
  { label: 'ewm', type: 'method' },
  { label: 'shift', type: 'method' },
  { label: 'diff', type: 'method' },
  { label: 'pct_change', type: 'method' },
  { label: 'astype', type: 'method' },
];

function pythonCompletions(context) {
  const word = context.matchBefore(/\w*/);
  if (!word || (word.from === word.to && !context.explicit)) return null;
  return completeFromList(COMPLETIONS)(context);
}

// @codemirror/lint's linter() debounces internally via `delay` -- it waits
// for `delay` ms of no further doc changes before calling this source, so
// no separate debounce/request-token bookkeeping is needed here.
const codeValidationLinter = linter(async (view) => {
  const code = view.state.doc.toString();
  if (!code.trim()) return [];
  let result;
  try {
    result = await validateCode(code);
  } catch {
    return [];
  }
  if (result.valid) return [];
  const doc = view.state.doc;
  return (result.violations || []).map((v) => {
    const lineNum = v.line >= 1 && v.line <= doc.lines ? v.line : 1;
    const line = doc.line(lineNum);
    return { from: line.from, to: line.to, severity: 'error', message: v.message };
  });
}, { delay: 800 });

const editorHighlightStyle = HighlightStyle.define([
  { tag: t.keyword, color: 'var(--accent-2)' },
  { tag: [t.function(t.variableName), t.function(t.definition(t.variableName)), t.definition(t.variableName)], color: 'var(--accent)' },
  { tag: t.variableName, color: 'var(--fg)' },
  { tag: t.string, color: 'var(--accent)' },
  { tag: t.number, color: 'var(--accent-2)' },
  { tag: t.bool, color: 'var(--accent-2)' },
  { tag: t.comment, color: 'var(--muted)', fontStyle: 'italic' },
  { tag: t.operator, color: 'var(--fg)' },
  { tag: t.propertyName, color: 'var(--accent-2)' },
]);

const editorTheme = EditorView.theme({
  '&': {
    color: 'var(--fg)',
    backgroundColor: 'var(--panel)',
    border: '1px solid var(--border)',
    borderRadius: '12px',
    fontSize: '13px',
    width: '100%',
  },
  '.cm-content': {
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    caretColor: 'var(--fg)',
    minHeight: '320px',
  },
  '.cm-scroller': { maxHeight: '60vh', overflow: 'auto' },
  '.cm-gutters': {
    backgroundColor: 'var(--panel)',
    color: 'var(--muted)',
    border: 'none',
    borderRight: '1px solid var(--border)',
  },
  '.cm-activeLine': { backgroundColor: 'var(--panel-strong)' },
  '.cm-activeLineGutter': { backgroundColor: 'var(--panel-strong)' },
  '&.cm-focused': { outline: '1px solid var(--accent-2)' },
  '.cm-tooltip': {
    backgroundColor: '#10141f',
    color: 'var(--fg)',
    border: '1px solid var(--border)',
  },
  '.cm-tooltip-autocomplete ul li[aria-selected]': {
    backgroundColor: 'var(--panel-strong)',
    color: 'var(--fg)',
  },
}, { dark: true });

export default function CodeEditor({ value, onChange }) {
  const containerRef = useRef(null);
  const viewRef = useRef(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    const state = EditorState.create({
      doc: value,
      extensions: [
        lineNumbers(),
        highlightActiveLine(),
        history(),
        bracketMatching(),
        closeBrackets(),
        indentOnInput(),
        syntaxHighlighting(editorHighlightStyle, { fallback: true }),
        python(),
        autocompletion({ override: [pythonCompletions] }),
        lintGutter(),
        codeValidationLinter,
        keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...historyKeymap, indentWithTab]),
        editorTheme,
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            onChangeRef.current(update.state.doc.toString());
          }
        }),
      ],
    });

    const view = new EditorView({ state, parent: containerRef.current });
    viewRef.current = view;

    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const currentValue = view.state.doc.toString();
    if (value !== currentValue) {
      view.dispatch({ changes: { from: 0, to: currentValue.length, insert: value } });
    }
  }, [value]);

  return <div ref={containerRef} className="code-editor" />;
}
