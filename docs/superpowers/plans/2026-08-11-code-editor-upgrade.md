# Richer Code Editor for Custom-Code Strategies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the plain `<textarea>` in `StrategyBuilder.jsx`'s custom-code panel with a CodeMirror 6 editor that has line numbers/bracket-matching/auto-indent, inline diagnostics sourced from the sandbox's AST safety check (with real line numbers), and static autocomplete for `df`/`pd`/`np`/OHLCV columns.

**Architecture:** Backend: `check_ast_safety`'s violations gain line numbers, and a new cheap `POST /strategies/validate-code` endpoint exposes them without spawning the sandbox subprocess. Frontend: a new self-contained `CodeEditor.jsx` component wraps CodeMirror 6, debounces calls to that endpoint via `@codemirror/lint`'s built-in `linter()` delay, and swaps in for the textarea with no other `StrategyBuilder.jsx` state changes.

**Tech Stack:** FastAPI/Pydantic v2 (backend, unchanged), React + CodeMirror 6 (`@codemirror/state`, `@codemirror/view`, `@codemirror/language`, `@codemirror/commands`, `@codemirror/lang-python`, `@codemirror/autocomplete`, `@codemirror/lint`) on the frontend.

## Global Constraints

- No changes to `run_custom_strategy`, the subprocess sandbox, or resource limits — only the pre-spawn static check's error *shape* changes (line numbers added), not its logic.
- `SandboxValidationError`'s existing joined-string message (`str(exc)`) must stay byte-for-byte compatible with today's format, since `_validate_custom_code` and existing tests (`tests/test_sandbox_executor.py`) assert on it via `match=`.
- The new `/strategies/validate-code` endpoint is advisory/UX-only; `_validate_custom_code`'s save-time call remains the sole authoritative gate — do not wire Save-button enablement to it.
- Follow the `/strategies` router's existing (no `/api` prefix) convention and its `Depends(get_current_user)` auth pattern — see CLAUDE.md's "Router prefixes are inconsistent" note.
- No new automated frontend tests — this repo has no frontend test suite (per CLAUDE.md); frontend tasks are verified by hand in the browser.
- Editor theme must be built from this app's existing CSS custom properties (`--bg`, `--panel`, `--panel-strong`, `--fg`, `--muted`, `--accent-2`, `--border`), not a prebuilt CodeMirror theme package.

---

### Task 1: Structured, line-numbered violations in `check_ast_safety`

**Files:**
- Modify: `BackEnd/services/sandbox_executor.py:54-55` (`SandboxValidationError` class), `:74-104` (`_SafetyVisitor`), `:107-129` (`check_ast_safety`)
- Test: `BackEnd/tests/test_sandbox_executor.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `SandboxValidationError.violations` — a `list[dict]`, each `{"line": int | None, "message": str}`. Task 2 reads this attribute directly.

- [ ] **Step 1: Write the failing tests**

Add to `BackEnd/tests/test_sandbox_executor.py` (after `test_disallowed_import_rejected_by_ast_check`, around line 48):

```python
def test_disallowed_import_violation_includes_line_number():
    code = (
        "def generate_signals(df):\n"
        "    return df['close'] * 0\n"
        "import os\n"
    )
    with pytest.raises(SandboxValidationError) as exc_info:
        check_ast_safety(code)
    assert exc_info.value.violations == [
        {"line": 3, "message": "import of 'os' is not allowed"},
    ]


def test_missing_generate_signals_violation_anchors_to_line_one():
    code = "x = 1\n"
    with pytest.raises(SandboxValidationError) as exc_info:
        check_ast_safety(code)
    assert exc_info.value.violations == [
        {"line": 1, "message": "code must define a top-level function named 'generate_signals'"},
    ]


def test_syntax_error_violation_includes_line_number():
    code = "def generate_signals(df):\n    return df['close'] * 0\nif\n"
    with pytest.raises(SandboxValidationError) as exc_info:
        check_ast_safety(code)
    assert exc_info.value.violations[0]["line"] == 3
    assert "Syntax error" in exc_info.value.violations[0]["message"]


def test_validation_error_message_unchanged_for_existing_callers():
    code = (
        "import os\n"
        "def generate_signals(df):\n"
        "    return df['close'] * 0\n"
    )
    with pytest.raises(SandboxValidationError, match="import of 'os' is not allowed"):
        check_ast_safety(code)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd BackEnd && uv run pytest tests/test_sandbox_executor.py -v -k "violation"`
Expected: FAIL — `AttributeError: 'SandboxValidationError' object has no attribute 'violations'` (or similar) on all four new tests.

- [ ] **Step 3: Add `.violations` to `SandboxValidationError`**

In `BackEnd/services/sandbox_executor.py`, replace:

```python
class SandboxValidationError(SandboxError):
    """Code failed the pre-execution safety check (syntax or disallowed construct)."""
```

with:

```python
class SandboxValidationError(SandboxError):
    """Code failed the pre-execution safety check (syntax or disallowed construct).

    `.violations` is a list of {"line": int | None, "message": str} dicts for
    structured, line-anchored consumers (e.g. editor diagnostics). Falls back
    to a single {"line": None, "message": str(self)} entry when constructed
    without one, so callers that just want str(exc) are unaffected.
    """

    def __init__(self, message, violations=None):
        super().__init__(message)
        self.violations = violations if violations is not None else [{"line": None, "message": message}]
```

- [ ] **Step 4: Make `_SafetyVisitor` collect `{"line", "message"}` dicts instead of strings**

Replace the whole `_SafetyVisitor` class (`BackEnd/services/sandbox_executor.py:74-104`) with:

```python
class _SafetyVisitor(ast.NodeVisitor):
    def __init__(self):
        self.violations = []  # list of {"line": int, "message": str}

    def visit_Import(self, node):
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root not in ALLOWED_IMPORT_ROOTS:
                self.violations.append({
                    "line": node.lineno,
                    "message": f"import of {alias.name!r} is not allowed",
                })
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        root = (node.module or "").split(".")[0]
        if root not in ALLOWED_IMPORT_ROOTS:
            self.violations.append({
                "line": node.lineno,
                "message": f"import of {node.module!r} is not allowed",
            })
        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id in DISALLOWED_CALL_NAMES:
            self.violations.append({
                "line": node.lineno,
                "message": f"call to {node.func.id}() is not allowed",
            })
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id.startswith("__") and node.id.endswith("__"):
            self.violations.append({
                "line": node.lineno,
                "message": f"reference to {node.id!r} is not allowed",
            })
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self.violations.append({
                "line": node.lineno,
                "message": f"attribute access to {node.attr!r} is not allowed",
            })
        self.generic_visit(node)
```

- [ ] **Step 5: Update `check_ast_safety` to build and pass through structured violations**

Replace `check_ast_safety` (`BackEnd/services/sandbox_executor.py:107-129`) with:

```python
def check_ast_safety(code: str) -> None:
    """Raise SandboxValidationError if `code` doesn't meet the custom-code
    strategy contract: a top-level `generate_signals` function, imports
    limited to pandas/numpy, no eval/exec/open/etc., no dunder access."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SandboxValidationError(
            f"Syntax error: {e}",
            violations=[{"line": e.lineno, "message": f"Syntax error: {e.msg}"}],
        ) from e

    has_entrypoint = any(
        isinstance(node, ast.FunctionDef) and node.name == "generate_signals"
        for node in tree.body
    )

    visitor = _SafetyVisitor()
    visitor.visit(tree)

    violations = list(visitor.violations)
    if not has_entrypoint:
        violations.insert(0, {
            "line": 1,
            "message": "code must define a top-level function named 'generate_signals'",
        })

    if violations:
        raise SandboxValidationError(
            "; ".join(v["message"] for v in violations),
            violations=violations,
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd BackEnd && uv run pytest tests/test_sandbox_executor.py -v`
Expected: PASS — all tests, including the four new ones and every pre-existing one (message-based `match=` assertions must still pass unchanged).

- [ ] **Step 7: Commit**

```bash
cd BackEnd
git add services/sandbox_executor.py tests/test_sandbox_executor.py
git commit -m "$(cat <<'EOF'
Add line numbers to sandbox AST safety-check violations

SandboxValidationError.violations now carries structured {line, message}
dicts (str(exc) unchanged) so callers can anchor feedback to a specific
line -- needed for inline editor diagnostics.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `POST /strategies/validate-code` endpoint

**Files:**
- Modify: `BackEnd/routers/strategies.py`
- Test: `BackEnd/tests/test_strategy_code_validation.py`

**Interfaces:**
- Consumes: `SandboxValidationError.violations` from Task 1 (`list[dict]` of `{"line": int | None, "message": str}`).
- Produces: `strategies_router.CodeValidationRequest(code: str)`, `strategies_router.CodeViolation(line: int | None, message: str)`, `strategies_router.CodeValidationResponse(valid: bool, violations: list[CodeViolation])`, and `async def validate_code(payload: CodeValidationRequest, user: User = Depends(get_current_user)) -> CodeValidationResponse`. Task 3's frontend `validateCode(code)` API call expects the JSON shape `{"valid": bool, "violations": [{"line": int|null, "message": str}]}`.

- [ ] **Step 1: Write the failing tests**

Add to `BackEnd/tests/test_strategy_code_validation.py` (end of file, after `test_update_strategy_code_to_valid_code_succeeds`):

```python
@pytest.mark.asyncio
async def test_validate_code_endpoint_accepts_valid_code(session_factory, seeded):
    user = await _get_user(session_factory, seeded["user_id"])
    payload = strategies_router.CodeValidationRequest(code=VALID_CODE)
    result = await strategies_router.validate_code(payload, user=user)
    assert result.valid is True
    assert result.violations == []


@pytest.mark.asyncio
async def test_validate_code_endpoint_reports_line_numbered_violations(session_factory, seeded):
    user = await _get_user(session_factory, seeded["user_id"])
    code = (
        "def generate_signals(df):\n"
        "    return df['close'] * 0\n"
        "import os\n"
    )
    payload = strategies_router.CodeValidationRequest(code=code)
    result = await strategies_router.validate_code(payload, user=user)
    assert result.valid is False
    assert result.violations == [
        strategies_router.CodeViolation(line=3, message="import of 'os' is not allowed"),
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd BackEnd && uv run pytest tests/test_strategy_code_validation.py -v -k validate_code`
Expected: FAIL — `AttributeError: module 'routers.strategies' has no attribute 'CodeValidationRequest'`.

- [ ] **Step 3: Add the request/response models and endpoint**

At the end of `BackEnd/routers/strategies.py` (after `update_strategy`), add:

```python
class CodeValidationRequest(BaseModel):
    code: str


class CodeViolation(BaseModel):
    line: Optional[int] = None
    message: str


class CodeValidationResponse(BaseModel):
    valid: bool
    violations: list[CodeViolation] = []


@router.post("/validate-code", response_model=CodeValidationResponse)
async def validate_code(payload: CodeValidationRequest, user: User = Depends(get_current_user)):
    try:
        check_ast_safety(payload.code)
    except SandboxValidationError as e:
        return CodeValidationResponse(valid=False, violations=e.violations)
    return CodeValidationResponse(valid=True, violations=[])
```

This runs no subprocess (`check_ast_safety` is a pure `ast.parse` + tree walk) and writes nothing to the DB, so it stays cheap enough to call on every debounce tick from the editor.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd BackEnd && uv run pytest tests/test_strategy_code_validation.py -v`
Expected: PASS — all tests, including the two new ones.

- [ ] **Step 5: Run the full backend suite**

Run: `cd BackEnd && uv run pytest tests/ -v`
Expected: PASS — no regressions anywhere else.

- [ ] **Step 6: Commit**

```bash
cd BackEnd
git add routers/strategies.py tests/test_strategy_code_validation.py
git commit -m "$(cat <<'EOF'
Add POST /strategies/validate-code for live editor diagnostics

Cheap, auth-gated endpoint that runs only the static AST safety check
(no subprocess spawn, no DB write) and returns structured, line-numbered
violations. Advisory only -- save-time validation remains the
authoritative gate.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `CodeEditor` component (CodeMirror 6)

**Files:**
- Modify: `FrontEnd/package.json` (add dependencies)
- Modify: `FrontEnd/src/api/strategies.js` (add `validateCode`)
- Create: `FrontEnd/src/components/CodeEditor.jsx`

**Interfaces:**
- Consumes: `POST /strategies/validate-code` from Task 2, via `client.js`'s shared axios instance (same pattern as `strategies.js`'s existing exports).
- Produces: `export default function CodeEditor({ value: string, onChange: (code: string) => void })` — a drop-in replacement for the `<textarea>`, consumed by Task 4.

- [ ] **Step 1: Install CodeMirror packages**

Run: `cd FrontEnd && npm install @codemirror/state @codemirror/view @codemirror/language @codemirror/commands @codemirror/lang-python @codemirror/autocomplete @codemirror/lint`

Expected: `package.json`'s `dependencies` gains these seven packages; `package-lock.json` updates.

- [ ] **Step 2: Add `validateCode` to the strategies API module**

In `FrontEnd/src/api/strategies.js`, add after `updateStrategy`:

```js
export async function validateCode(code) {
  const { data } = await client.post('/strategies/validate-code', { code });
  return data;
}
```

- [ ] **Step 3: Write `CodeEditor.jsx`**

Create `FrontEnd/src/components/CodeEditor.jsx`:

```jsx
import React, { useEffect, useRef } from 'react';
import { EditorState } from '@codemirror/state';
import { EditorView, keymap, lineNumbers, highlightActiveLine } from '@codemirror/view';
import {
  bracketMatching,
  indentOnInput,
  syntaxHighlighting,
  defaultHighlightStyle,
} from '@codemirror/language';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import {
  closeBrackets,
  closeBracketsKeymap,
  autocompletion,
  completeFromList,
} from '@codemirror/autocomplete';
import { linter, lintGutter } from '@codemirror/lint';
import { python } from '@codemirror/lang-python';
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
}, { delay: 500 });

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
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        python(),
        autocompletion({ override: [pythonCompletions] }),
        lintGutter(),
        codeValidationLinter,
        keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...historyKeymap]),
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
```

Note: the editor initializes once on mount (empty dependency array — `value` is only read as the *initial* doc); subsequent external `value` changes (e.g. switching templates) are synced via the second effect, and internal edits flow out via `onChange` without feeding back into the first effect. This is the standard CodeMirror-in-React pattern and avoids tearing down/recreating the editor (and losing cursor position/undo history) on every keystroke.

- [ ] **Step 4: Manual smoke test in isolation**

Run: `cd FrontEnd && npm run dev`

Temporarily render `<CodeEditor value="def generate_signals(df):\n    return df['close'] * 0\n" onChange={console.log} />` from any existing page (e.g. paste it at the top of `DataPage.jsx`'s return, or use the browser console) to confirm:
- Line numbers render down the left gutter.
- Typing updates the doc and `console.log` fires with the new text.
- Typing `import os` on its own line and waiting ~1s shows a red squiggle/gutter dot on that line with a hover tooltip reading "import of 'os' is not allowed".

Remove the temporary render before moving to Task 4 (Task 4 wires it in properly).

- [ ] **Step 5: Commit**

```bash
cd FrontEnd
git add package.json package-lock.json src/api/strategies.js src/components/CodeEditor.jsx
git commit -m "$(cat <<'EOF'
Add CodeMirror-based CodeEditor component

Self-contained editor with line numbers, bracket matching, Python
auto-indent, static df/pd/np autocomplete, and inline lint diagnostics
sourced from POST /strategies/validate-code. Not yet wired into
StrategyBuilder.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Wire `CodeEditor` into `StrategyBuilder` and verify end-to-end

**Files:**
- Modify: `FrontEnd/src/components/StrategyBuilder.jsx:1,189-195`
- Modify: `FrontEnd/src/styles.css:272-278`

**Interfaces:**
- Consumes: `CodeEditor` from Task 3 (`{ value, onChange }` props).
- Produces: nothing new for later tasks — this is the final integration task.

- [ ] **Step 1: Import `CodeEditor` in `StrategyBuilder.jsx`**

At the top of `FrontEnd/src/components/StrategyBuilder.jsx`, change:

```jsx
import React, { useState } from 'react';
```

to:

```jsx
import React, { useState } from 'react';
import CodeEditor from './CodeEditor.jsx';
```

- [ ] **Step 2: Replace the textarea**

In `FrontEnd/src/components/StrategyBuilder.jsx`, replace (lines 189-195):

```jsx
          <textarea
            className="code-editor"
            rows={18}
            spellCheck={false}
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
```

with:

```jsx
          <CodeEditor value={code} onChange={setCode} />
```

No other changes to `StrategyBuilder.jsx` — `code` state, `canSave`, and `handleSave` are untouched.

- [ ] **Step 3: Update `.code-editor` CSS for the CodeMirror wrapper**

In `FrontEnd/src/styles.css`, replace (lines 272-278):

```css
.code-editor {
  width: 100%;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.5;
  min-height: 320px;
}
```

with:

```css
.code-editor {
  width: 100%;
}
```

(Sizing, font, colors, and border-radius now live in `CodeEditor.jsx`'s `editorTheme`, since `.code-editor` is now a wrapper `<div>` around CodeMirror's own DOM rather than the styled element itself.)

- [ ] **Step 4: Manually verify the full custom-code flow in the browser**

Run: `cd BackEnd && uv run uvicorn app:app --reload` (one terminal) and `cd FrontEnd && npm run dev` (another).

In the browser, log in, go to a project's Strategies page, open the Strategy Builder, switch to "Custom Python Code", and verify:
- The editor renders with line numbers and the pre-filled `CUSTOM_CODE_TEMPLATE`, styled consistently with the rest of the dark UI (no white-on-white, matches panel/border colors).
- Typing after a line ending in `:` auto-indents the next line.
- Typing `df.` triggers an autocomplete popup listing `open`/`high`/`low`/`close`/`volume`/etc.
- Typing `import os` on a new line shows an inline diagnostic on that line within ~1s of pausing.
- Fixing the code (removing the bad line) clears the diagnostic.
- Entering a strategy name and clicking "Save Strategy" with valid code still saves successfully (existing save-time validation + backtest flow unaffected).
- Saving with code that fails validation still surfaces the existing save-time error box (`err?.response?.data?.detail`) as before — the new inline diagnostics are additive, not a replacement for that check.

- [ ] **Step 5: Commit**

```bash
cd FrontEnd
git add src/components/StrategyBuilder.jsx src/styles.css
git commit -m "$(cat <<'EOF'
Wire CodeMirror editor into the custom-code strategy panel

Replaces the plain textarea with CodeEditor, giving the custom-code
panel line numbers, auto-indent, bracket matching, autocomplete, and
inline safety-check diagnostics.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
