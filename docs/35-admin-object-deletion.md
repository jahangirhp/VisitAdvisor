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
