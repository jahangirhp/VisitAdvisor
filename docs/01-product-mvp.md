# VisitAdvisor — Product Definition & MVP Scope

> **Superseded camera architecture — 2026-09-20:** The user authorized moving
> recognition to the server with temporary frame uploads and no phone model
> download. See [the server camera trial](17-server-camera-trial.md). On-device
> statements below describe the earlier prototype.

**Version:** 0.1  
**Status:** Revised MVP scope — live-camera extension 2026-09-19

## Live-camera scope extension — 2026-09-19

The user requested continuous recognition while moving an iPhone 12 Pro camera
between objects, without manual captures, gallery uploads, or transmitting camera
imagery. The bounded prototype in `16-live-camera-prototype.md` supersedes the
camera/upload flow below for this client. It runs local frame inference and shows
tentative candidates; existing server OCR and visual upload APIs remain separate.
This extension does not declare Phase 5 accuracy gates passed or add global
recognition, recording, custom model training, or a complete offline visitor app.

## 1. Product Goal

VisitAdvisor is a location-aware AI guide that connects digital knowledge to real-world places and objects.

A visitor selects a supported destination before or during a visit. The system has already collected and prepared knowledge about that destination. When the visitor points the phone camera at an exhibit label, plaque, sign, object, or place, the app identifies the most likely entity and automatically presents useful information.

The primary experience is **automatic discovery**, not chatbot Q&A.

The app should proactively provide:

- a short introduction
- key facts
- a concise story
- historical context
- why the place/object matters
- optional deeper information

Conversational AI is secondary and can be added later.

## 2. Core User Problem

Visitors often see an object, building, artwork, monument, or historical place but lack immediate context. Existing solutions frequently require:

- reading long plaques
- searching the web manually
- scanning dedicated QR codes
- installing venue-specific apps
- knowing what to ask

VisitAdvisor should reduce this friction to:

> Select destination → point camera → recognize → understand.

## 3. MVP Target User

A person visiting a museum, historical site, landmark, exhibition, or other curated public location with a smartphone.

The MVP does not need to support every type of visitor or every type of location. It only needs to prove that the core experience works reliably in one real destination.

## 4. MVP User Flow

```text
Select supported location
        ↓
Open camera / upload image
        ↓
Visual matching compares the object photo with reference images
        ↓
Optional OCR adds evidence when a label is visible
        ↓
System ranks objects within the selected location
        ↓
Best object/place is identified
        ↓
Show prepared content automatically
        ↓
Facts + short story + history
```

If recognition confidence is low, the system may show up to three likely matches for the user to choose from.

## 5. MVP Scope

### Included

1. One real destination for the first vertical slice.
2. 100–500 real objects/points of interest if the data source allows it.
3. Location creation and storage.
4. Import from one known, trusted source such as:
   - official collection API
   - official website
   - structured dataset
5. AI-assisted transformation of source data into structured object knowledge.
6. Storage of original source references.
7. Prepared visitor content for each object:
   - title/name
   - aliases
   - short description
   - key facts
   - short story
   - historical context
   - interesting fact when supported by evidence
8. Text embeddings for semantic retrieval.
9. Visual recognition from full-object and partial-object photos without labels;
   OCR is another recognition path. Evaluate a bounded set of objects and places,
   including paintings, statues, artifacts, rocks, and buildings where references exist.
10. Object matching constrained to the selected location.
11. Mobile-first visitor result page.
12. Basic source traceability.

### Explicitly Excluded From MVP

Do not build these until the vertical slice is proven:

- automatic internet-wide source discovery
- custom-trained LLMs
- custom OCR models
- custom computer-vision models
- millions of reference images
- indoor positioning
- complex knowledge graphs
- automatic conflict-resolution engine
- multi-location global scale
- microservices
- Kubernetes
- Kafka
- complex event architecture
- offline-first location packages
- advanced personalization
- social features
- payments
- audio tours
- full chatbot experience

## 6. Product Principles

### Precompute Expensive Work

Crawling, extraction, content preparation, and embeddings should happen before the visitor needs the information whenever possible.

### Location Narrows Recognition

The selected destination is a primary recognition signal. Do not search globally when the location is known.

### Sources Remain the Evidence

AI-generated text is never the canonical source of truth. Important generated content must retain references to the source data from which it was produced.

### Automatic Content First

The visitor should not need to formulate a question to receive value.

### Progressive Depth

Show concise content first and allow deeper exploration only when requested.

## 7. MVP Success Criteria

The MVP is successful when all of the following are demonstrated on one real location:

1. Real source data can be imported.
2. At least 100 real objects can be represented consistently.
3. AI can transform source data into the required structured format with acceptable quality.
4. Sources remain linked to imported/generated knowledge.
5. Embeddings can retrieve relevant object knowledge.
6. Label-free recognition is evaluated on independent full-object and partial-view
   photos; optional OCR can read representative labels or plaques.
7. The matching layer correctly identifies the intended object in most controlled tests.
8. A visitor receives prepared facts/story/history within a reasonable interactive response time.
9. Incorrect matches can be detected or corrected without breaking the experience.
10. The team can estimate ingestion cost, AI cost, and recognition accuracy from real usage.

## 8. Primary MVP Metrics

Track at minimum:

- import success rate
- objects successfully created
- extraction failure rate
- OCR success rate
- object-match accuracy
- time from scan to result
- percentage of scans requiring manual candidate selection
- AI processing cost per imported object/location
- percentage of generated content with valid source references

## 9. First Validation Scenario

A strong first demonstration is:

```text
One museum
   ↓
Import 100–500 exhibits
   ↓
Generate structured knowledge
   ↓
Visitor photographs the exhibit, including a partial view
   ↓
Visual matching uses source-linked reference images
   ↓
System identifies the exhibit
   ↓
Visitor immediately sees:
   • why it matters
   • 3–5 facts
   • short story
   • history
```

This vertical slice is the product milestone that should be proven before expanding the architecture.

## Phase 5 scope correction — 2026-09-11

Label-free recognition, including useful partial views, is a core requirement.
The previous OCR-only scope is superseded. This is a target, not a claim that any
arbitrary fragment is identifiable. The bounded pilot and acceptance criteria are
in `07-visual-recognition-pilot.md`. The API supports OCR and an experimental
visual candidate mode; automatic visual identification remains unvalidated.

The long-term target is almost any observable object. The MVP requires a
user-selected visit location and recognizes known entities with reference coverage.
Museums provide the initial dataset, not the boundary of the product.
