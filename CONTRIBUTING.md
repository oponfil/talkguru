# Coding Style Guide & Conventions

This document is the **Style Guide** for the **DraftGuru** project.
It describes the coding standards that must be strictly followed.

## 1. Imports

### Core Rule
All `import` and `from ... import ...` statements must be **at the top of the file**.

### No Lazy Imports
❌ Lazy imports (importing inside functions) are strictly prohibited.
✅ Exception: Breaking circular dependencies (requires `# circular dependency` comment).

### Order (PEP 8)
1. **Standard Library** (`os`, `sys`, `asyncio`, `typing`)
2. **Third Party** (`telegram`, `httpx`, `supabase`)
3. **Local Application** (`config`, `database`, `clients`)

## 2. Naming Conventions

- **Classes**: `PascalCase` (`UserContext`)
- **Functions and Variables**: `snake_case` (`generate_response`)
- **Constants**: `UPPER_CASE` (`MAX_RETRIES`)
- **Private Methods**: `_snake_case` (with `_` prefix)

## 3. Formatting and Style

- **Line Length**: 120 characters
- **Quotes**: Double quotes `"` (Black standard)
- **Type Hinting**: Mandatory for all functions (`def func(a: int) -> bool:`)
- **Docstrings**: In **Russian**, mandatory for public functions
- **File Header**: Every Python file must start with a comment in the format `# path/to/file.py — Short description`

## 4. Asynchronous Code (Async/Await)

All network calls must be `async`:
- Telegram API
- Supabase
- x402gate / OpenRouter

For CPU-bound tasks:
```python
result = await asyncio.to_thread(blocking_function, arg1)
```

## 5. Logging

- Format: `print(f"{get_timestamp()} [COMPONENT] Message")`
- ❌ **Do not** wrap errors in `if DEBUG_PRINT:` — they must always be visible
- ✅ Informational output — `if DEBUG_PRINT:`
- Levels: `[DEBUG]`, `[INFO]`, `[WARNING]`, `[ERROR]`

## 6. DRY (Don't Repeat Yourself)

❌ Do not duplicate logic.
✅ Extract common code into separate functions.

## 7. Database Access

❌ Direct database calls outside the `database/` folder are prohibited.
❌ Do not import `supabase` or `run_supabase` in `handlers/`, `utils/`, `clients/`, or other application layers.
✅ Any data access must go exclusively through functions in `database/*.py`.
✅ If a new query is needed, first add a separate function in `database/`, and then call it from other modules.

## 8. Backward Compatibility

❌ Not supported. Remove old code immediately.
✅ One format, no fallbacks.

## 9. Linting (Ruff)

We use **ruff** for code linting. Before every commit:

```bash
ruff check .
```

- All errors must be fixed before committing
- `# noqa: <RULE>` is permissible only with a justification (e.g., `# noqa: E402` for intentional import ordering)

## 10. Git Commits

### Format: Conventional Commits
```
<type>(<scope>): <subject>

<body>
```

**Types:** `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

### Completeness Rule
❌ It is prohibited to make a commit where the `<body>` does not describe **all** changes.
✅ Every modified file / feature must be reflected in the `<body>` as a list `- ...`.

### Example
```
feat(bot): add /start command with greeting message

- Register user in Supabase on first contact
- Send greeting with bot description
- Update README with /start usage
```

### PowerShell Workflow
```powershell
# Write commit message to a file
[System.IO.File]::WriteAllText(".git-commit-msg.txt", "feat(scope): description")

# Commit
git add -A
git commit -F .git-commit-msg.txt
```

## 11. Testing — TDD (Test-Driven Development)

The project follows the **TDD** approach — Test-Driven Development.

### Principle

1. 🔴 **Red** — Write a test for new functionality. The test must fail.
2. 🟢 **Green** — Write the minimal code to make the test pass.
3. 🔵 **Refactor** — Refactor the code without breaking the tests.

### Rules

- ❌ Merging code without tests is **prohibited**
- ✅ Every new function / handler / utility **must** have tests
- ✅ Tests must cover **all modules** — with no exceptions
- ✅ External dependencies are **mocked** — `.env` is not required
- ✅ GitHub Actions automatically run tests on push and PR

### Execution

```bash
pytest tests/ -v
```

All tests **must** pass before every commit.

## 12. Code Review Checklist

1. [ ] **DRY**: No duplication
2. [ ] **Imports**: At the top of the file, no lazy imports
3. [ ] **Style**: Naming and typing
4. [ ] **Async**: Network calls via `await`
5. [ ] **Logging**: Errors without `if DEBUG_PRINT`
6. [ ] **DB Access**: No direct database calls outside `database/`
7. [ ] **Constants**: Everything in `config.py`
8. [ ] **Linting**: `ruff check .` passes without errors
9. [ ] **Tests**: `pytest tests/ -v` passes without errors
10. [ ] **Commits**: Conventional Commits, in English
