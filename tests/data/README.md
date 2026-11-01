# Visual test data (dev UI)

Managed from the UI at http://localhost:8003/api/v1/visual-evaluation
(`docker compose --profile visual up -d --build visual-test`, env in `.env.test`).

```
references/<type>/<name>.jpg    one image = one object, embedded into the TEST database
positive/<series>/<photo>.jpg   test photos
```

- Upload a batch of references (pick a type): each image becomes a DB object and is embedded.
- A photo `<name>.jpg`, `<name>-2.jpg` or `<name>.partial.jpg` expects reference `<name>`.
  Choosing "partial view" when uploading adds the `.partial` suffix.

- "Build embeddings" uses the application's own pipeline (`prepare_image`,
  `encoder.describe(regions=True)`, `VisualRepository.publish`) into the database
  named by `MONGODB_DATABASE` in `.env.test` (must contain `test`).
- Uploading or deleting references rebuilds automatically; "Rebuild embeddings" forces it.
- Each series is a selectable dataset; run history is stored in the test database.
- `visual/` holds legacy material and is unused.
