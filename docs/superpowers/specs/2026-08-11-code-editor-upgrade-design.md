# Richer Code Editor for Custom-Code Strategies

## Context

`StrategyBuilder.jsx`'s custom-code panel uses a plain `<textarea className="code-editor">` for `generate_signals(df)` source, deliberately kept simple in [[2026-08-11-custom-strategy-code-design]] ("no CodeMirror/Monaco dependency for this first pass", explicitly listed under that spec's "out of scope"). This spec is that follow-up: a real code-editing experience — line numbers, bracket matching, auto-indent, inline error markers sourced from the sandbox's AST safety check, and static autocomplete — without touching sandbox execution, the rules-mode UI, or the save/backtest flow.

## Goal

Make writing `generate_signals(df)` feel like using a real editor, and surface the sandbox's static safety-check violations inline (with line numbers) as the user types, instead of only as a save-time error toast.

## Editor library

CodeMirror 6, chosen over Monaco: ~150-200KB gzipped vs. Monaco's multi-MB footprint, modular packages, and its extension model themes cleanly against this app's existing dark CSS variables rather than fighting Monaco's own default styling.

New frontend dependencies: `codemirror`, `@codemirror/lang-python`, `@codemirror/autocomplete`, `@codemirror/lint`, `@codemirror/state`, `@codemirror/view`.

## Backend: structured violations

`services/sandbox_executor.py`'s `_SafetyVisitor` currently appends plain strings to `violations`; `check_ast_safety` joins them into one message with no line numbers. Every AST node already carries `.lineno`, so:

- `_SafetyVisitor.violations` becomes a list of `{"line": int, "message": str}` dicts instead of strings (one dict per rejected import/call/dunder-access, using the offending node's `.lineno`).
- The missing-`generate_signals`-entrypoint check (no specific node) reports `"line": 1`.
- A `SyntaxError` from `ast.parse` reports `"line": e.lineno` (which can be `None` for some syntax errors — the frontend treats `null` as "no gutter anchor, show as a general diagnostic").
- `SandboxValidationError` gains a `.violations` attribute holding this list. Its existing joined-string message (`"; ".join(...)`, used as `str(exc)`) is unchanged, so `_validate_custom_code`'s `HTTPException(400, f"Invalid strategy code: {e}")` and the existing `pytest.raises(SandboxValidationError, match=...)` assertions in `tests/test_sandbox_executor.py` don't need to change.

New endpoint in `routers/strategies.py`, following the router's existing `/strategies` prefix (no `/api`, per CLAUDE.md's router-prefix note) and its `Depends(get_current_user)` auth pattern:

```
POST /strategies/validate-code
body: {"code": str}
200: {"valid": true}
   | {"valid": false, "violations": [{"line": int | null, "message": str}, ...]}
```

Implementation just calls `check_ast_safety(code)` and catches `SandboxValidationError`, reading `.violations` off it. No subprocess is spawned (pure `ast.parse` + a tree walk), no DB write — cheap enough to call on a debounce. This endpoint is advisory/UX-only; `_validate_custom_code`'s save-time call to `check_ast_safety` (in `create_strategy`/`update_strategy`) remains the sole authoritative gate, unchanged.

## Frontend: `CodeEditor` component

New file `FrontEnd/src/components/CodeEditor.jsx`, props: `value: string`, `onChange(code: string): void`. Self-contained — owns its own debounce-and-validate cycle rather than pushing that logic into `StrategyBuilder.jsx`.

- **Extensions assembled explicitly** (not the bundled `basicSetup`, to keep the exact feature set intentional): line numbers, bracket matching, `closeBrackets`, `highlightActiveLine`, undo/redo history, and `@codemirror/lang-python`'s language support (which provides Python-aware indentation, e.g. auto-indenting the line after a trailing `:`).
- **Diagnostics**: on a ~500ms debounce after the value stops changing, POST the current code to `/strategies/validate-code` and feed the result into a `@codemirror/lint` `linter()` extension — underline + gutter marker on the violation's line, message on hover. A violation with `line: null` anchors to line 1 so it's still visible. Stale in-flight requests are ignored if a newer edit has already fired a new request (guard by request token/generation counter, not `AbortController`, since axios's default client wrapper in `client.js` doesn't need changing for this).
- **Theme**: a custom `EditorView.theme({...})` built from this app's existing CSS custom properties (`--bg`, `--panel`, `--fg`, `--muted`, `--accent`, `--border`) rather than a prebuilt CodeMirror theme package, so the editor matches the surrounding dark UI instead of reading as a bolted-on widget.
- **Autocomplete**: one static completion source registered via `autocompletion({override: [source]})` — not full pandas IntelliSense, just prefix-matching against a fixed list: `df`, `pd`, `np`, `generate_signals`, the five OHLCV column names (`open`, `high`, `low`, `close`, `volume`), and a short list of pandas methods already referenced in the template/help text (`rolling`, `mean`, `std`, `ewm`, `shift`, `diff`, `pct_change`, `astype`).

## Wiring into `StrategyBuilder.jsx`

Replace:
```jsx
<textarea className="code-editor" rows={18} spellCheck={false} value={code} onChange={(e) => setCode(e.target.value)} />
```
with:
```jsx
<CodeEditor value={code} onChange={setCode} />
```
No other state/prop changes — `code`, `canSave`, `handleSave` are untouched. The Save button's enablement (`canSave`) stays based only on non-empty name + code, same as today; it is **not** gated on live-validation state, since save-time validation (`_validate_custom_code`) already blocks and reports invalid code authoritatively, and gating on a debounced client-side check would just introduce a race between "still validating" and "click Save" for no real benefit.

`FrontEnd/src/styles.css`'s `.code-editor` rule (sizing for the old `<textarea>`) is replaced with equivalent sizing/border rules targeting CodeMirror's root class (`.cm-editor`).

## Testing

- Backend: extend `tests/test_sandbox_executor.py` with assertions on `.violations` line numbers for a couple of known-bad snippets (e.g. `import os` on a known line number → a violation dict with that `line`). Add a router-level test (new file or extending `tests/test_strategy_code_validation.py`) for `POST /strategies/validate-code`: valid code → `{"valid": true}`; invalid code → `{"valid": false, "violations": [...]}` with expected line numbers; unauthenticated request → 401.
- Frontend: no test suite exists for `FrontEnd/` per CLAUDE.md, and this feature doesn't change that — verified by hand in the browser: typing disallowed code shows an inline marker on the right line, typing `df.` offers the static completions, and the existing save/backtest flow for a custom-code strategy still works end to end.

## Explicitly out of scope

- Full pandas/numpy API autocomplete (e.g. via a language server) — the static list covers the documented/templated surface only.
- Gating the Save button on live-validation state.
- Any change to `run_custom_strategy`, the subprocess sandbox, or resource limits — this spec only touches the pre-spawn static check's error *shape* (adding line numbers), not its logic.
- Real-time validation transport other than simple debounced HTTP polling (e.g. no WebSocket).
