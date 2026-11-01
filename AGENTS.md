# AGENTS.md

## General Instructions

- Always inspect the existing code before making changes.
- Understand the task before implementing a solution.
- Prefer simple, clean, maintainable solutions.
- Avoid overengineering and unnecessary dependencies.
- Respect the existing architecture and coding conventions.
- Do not modify unrelated code or functionality.
- Never delete existing data without explicit authorization.

## Coding Standards

- Follow Python best practices and PEP 8.
- Write readable, modular, reusable code.
- Use meaningful names and type hints.
- Avoid code duplication.
- Keep functions small and focused.
- Write comments only when necessary.
- Use English for code, comments, and documentation.

## Development Workflow

1. Analyze the task and relevant files.
2. Implement changes incrementally.
3. Add or update tests when necessary.
4. Run relevant tests after modifications.
5. Review changes for regressions.
6. Summarize what was changed and report any issues.

## Security

- Never hardcode secrets or credentials.
- Use environment variables for sensitive configuration.
- Validate external inputs.
- Do not expose sensitive information in logs.
- Never commit `.env` files or runtime databases.

## Git Rules

- Work on a dedicated branch.
- Make small, descriptive commits.
- Never force-push or modify Git history without permission.
- Do not merge into `main` without authorization.

## Communication

- Be concise and technical.
- Explain important architectural decisions.
- Report errors and limitations clearly.
- Never claim a task or test succeeded without verification.
- Ask questions only when essential information is missing.
- Do not implement features beyond the requested scope.