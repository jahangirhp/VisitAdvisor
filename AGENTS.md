# VisitAdvisor development

- Read all documents in `/docs` before major changes.
- Preserve MVP scope; implement only the phase requested by the user.
- Use Python 3.12+ and FastAPI for the backend, MongoDB Atlas for storage,
  and the official PyMongo async API (never Motor).
- Prefer simple, explicit implementations. Avoid premature microservices,
  generic architectural layers, and infrastructure not currently required.
- Preserve source provenance in future knowledge processing. Keep AI-generated
  knowledge separate from canonical source evidence.
- Never commit credentials or expose connection details in errors or logs.
- Run `pytest`, `ruff check .`, `ruff format --check .`, and `mypy app`
  before completing significant changes.
