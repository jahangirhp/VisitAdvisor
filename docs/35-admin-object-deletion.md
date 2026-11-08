# Admin object deletion

Implemented on `feat/guided-reference-capture`, updated from the current deployed
`main` so the mobile, Save and recognition fixes remain included.

Object details now include **Delete object**. A dialog names the selected object,
explains permanent deletion of photos and embeddings, and offers Cancel or Delete.
There is no deletion request until confirmation. Errors stay inside the dialog;
a failed request can be retried. A retry that finds the object already gone closes
successfully, handling a lost successful response. After deletion the collection
refreshes; deleting the last item on a later page returns to the previous page.
Routine success uses the existing screen-reader status instead of a bottom toast.

The admin-token-protected
`DELETE /api/v1/admin/venues/{location_id}/objects/{object_id}` checks that the
object belongs to the selected venue. It deletes the canonical object (including
its prepared content), private photos and masks, `visual_references`,
`visual_vectors`, and `knowledge_chunks`. Related records are selected by the
verified globally unique object ID, covering all model versions, old/staged sets,
and historical location associations. Other objects and their data remain intact.
Linked canonical source records remain because they can be shared.

Cleanup uses one [PyMongo async transaction](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/crud/transactions/)
with snapshot reads and majority commit. Failure rolls back every collection.
The operation has a twenty-second deadline and returns a safe retryable error;
a repeated request for a deleted object returns 404. Deletion, photo upload,
reference publication and photo removal share the existing process lock, preventing
an in-flight admin upload or publication from recreating removed data. This keeps
the existing one-worker admin deployment contract; external import/preparation
scripts are independent writers.

After commit, the affected in-process camera reference caches are invalidated
immediately while retaining model weights. Affected default/selected venue caches
reload before serving another catalog or recognition result; in-flight recognition
retains its reference-version recheck. The object's geometry cache is also removed.
No model training, new collection, background worker or queue is introduced.

Backend coverage includes authentication, venue ownership, invalid/missing IDs,
cleanup of active/old/staged data, shared-source and other-object preservation,
rollback at each cleanup step, sanitized failures, retries, busy uploads and
publication, and immediate camera cache invalidation with model reuse. Browser
coverage includes confirmation/cancel, named objects rendered as text, failed and
lost-response retries, mobile dialog bounds, collection refresh and pagination.

The real Atlas integration check used uniquely named temporary collections in the
existing authorized database. An injected failure after reference/vector removal
returned 503 and rolled back all changes; retry returned 204 and removed all three
versions of each related record. The unrelated fixture object and shared source
remained, and another delete returned 404. Temporary collections were removed.
Existing application records were not modified.

Deleting a partially uploaded object also clears its pending browser capture
draft, so Retry save cannot continue targeting the removed record. That flow
passed separately in Chromium, WebKit and the iPhone profile.

## Release verification

Required checks passed: 544 Python tests (two opt-in skips), Ruff lint/format,
`mypy app`, and 35 JavaScript tests. Browser coverage passed 129 distinct admin
cases across Chromium, WebKit and iPhone emulation (three opt-in skips). One
existing synthetic WebKit camera-start case passed on isolated rerun. Eighteen
hosted preview cases were covered; the iPhone Save check passed after warming a
preview instance that had scaled to zero. That navigation delay was separate from
the delete flow. Production confirmation checks passed in all three profiles.
Browser deletion requests use mocked records; the real Atlas transaction check
above separately verifies deletion and rollback without changing existing data.

Revision `visitadvisor-api-delete-393b43942940` now serves 100% of traffic from
application commit `393b439` on `feat/guided-reference-capture`. Image digest:
`sha256:34e63cf1ed16d67a59c09a9d59479090446b37fa28f6fc6beb256e9e7e42197b`.
Cloud Build: `2b67eaad-bc8c-463e-a0f5-3283d2072250`.

Production health/database, seven asset hashes/cache headers, unauthorized delete
rejection, an authenticated missing-object transaction (404), runtime identity,
environment, CPU/memory and one warm instance were checked. All 115 existing
catalog objects and reference version
`247a6f5b008a8ea79f2feef8ee4226ab5ed9881d00c2d1ed2b31dfd7e0857664` were retained.
No existing application object, source, photo or reference was deleted during
validation. The preceding `visitadvisor-api-mobile-68655a2f9d79` revision remains
available for traffic rollback. `main` was not changed by this feature release.
