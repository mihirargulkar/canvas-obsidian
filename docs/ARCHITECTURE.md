# How this works, and the ideas behind it

Every technique in this project, why it's here, and a plain-language version you
can say out loud. Read top to bottom once; after that use it as a lookup.

---

## The shape of the whole thing

```
Canvas API  ->  ingest  ->  notes/  ->  extract  ->  vault/     (knowledge graph)
                              |
                              +------>  chunk + embed  ->  index/   (search)
                                                              |
                                                        MCP server
                                                              |
                                              Claude / Gemini / Cursor
```

Five stages: **get the files, read them, understand them, index them, serve
them.** Everything below hangs off one of those.

**Out loud:** "It's a pipeline. It pulls course files out of Canvas, uses a
vision model to turn slides into text, extracts the concepts and links them
across lectures, indexes everything for search, and exposes it to an LLM over
MCP."

---

## 1. Getting the data

### REST APIs and token auth

Canvas exposes a REST API. You authenticate with a personal access token sent in
an `Authorization: Bearer` header. The `canvasapi` library wraps it so you get
Python objects instead of raw JSON.

**Pagination** matters here. An API that could return thousands of rows returns
them in pages with a `Link` header pointing at the next one. `canvasapi` returns
a lazy `PaginatedList` that fetches pages as you iterate, which is why the code
does `list(c.get_assignments())` when it needs them all at once.

### The term-detection problem

Canvas returns every course you've ever enrolled in. Filtering to "this
semester" sounds trivial and isn't, because institutions don't fill in term
metadata consistently. The code layers three heuristics ([canvas.py:111](../canvas_vault/canvas.py:111)):

1. Drop terms with an end date in the past. Correct where the data exists.
2. Group what's left by the longest digit run in the term name (`202650_2A` and
   `202650_2B` both give `202650`) and keep the newest group. Handles schools
   running concurrent sessions.
3. If no term name has digits at all, fall back to the highest term id, since
   Canvas ids increase over time.

**Out loud:** "Real LMS data is messy. Half the schools never set term end dates,
so I had to layer a few heuristics instead of trusting one field."

### Graceful degradation

Instructors can restrict individual tabs. A locked Files tab throws a 403. The
first version let that exception propagate, so one restricted course killed
deadlines for every class. Now each course is wrapped in its own try/except and
a failure prints a warning and continues.

**The concept is fault isolation.** One unit of work failing should not fail the
batch. Worth knowing by name.

---

## 2. Reading the files

### Why a vision model

A `.pptx` is a zip of XML. You can pull the text out, but you lose the diagram
labels, the equations rendered as images, and the handwritten annotations. So the
slides get converted to PDF (LibreOffice in headless mode, `soffice --convert-to
pdf`) and sent to a **multimodal model** that reads them as images.

Multimodal just means the model accepts more than text. Gemini takes the PDF
pages as visual input and returns markdown.

Text formats (`.ipynb`, `.md`, `.txt`) skip the model entirely and get parsed
directly. No point paying a model to read JSON.

### Content-hash caching

The expensive step is the model call, and the free tier is capped per day. So
every file's bytes get hashed (SHA-256) and the transcription is stored under
that hash. Unchanged file, same hash, cache hit, no API call.

The subtlety: the cache key isn't just the file. It's a **"recipe"**, a hash of
the file *plus the prompt plus the model name*. Change the prompt and every
entry correctly invalidates, because the old output no longer reflects what the
current code would produce.

**Out loud:** "Cache keys should cover every input that affects the output. If
you key on the file alone, changing your prompt silently serves stale results."

### Idempotency and resumability

Running the pipeline twice produces the same result as running it once. That's
**idempotency**, and it's what makes a daily cron job safe. Combined with
caching, it also gives resumability: hit a rate limit halfway through and the
next run picks up where you stopped instead of starting over.

---

## 3. Building the knowledge graph

### Two-pass extraction

**Pass 1** sends each lecture to the model and asks for concepts, definitions,
and which concepts relate to each other, returned as JSON. This is
**structured output**: you set `response_mime_type: application/json` so the
model returns parseable data instead of prose.

**Pass 2** merges concepts across lectures by canonical name. If "Gradient
Descent" appears in weeks 2, 4 and 7, that becomes one node linked to all three
lectures rather than three separate nodes.

That merge step is **entity resolution**: deciding when two mentions refer to the
same real thing. It's a classic hard problem (think deduplicating customer
records) and here it's done with a simple canonical-name match. That's also why
link quality drops as the corpus grows, which is the current top bug.

**Out loud:** "The cross-lecture linking is the interesting part. Anyone can
summarize one lecture. Recognizing that the thing in week 7 is the same thing
from week 2 is entity resolution, and it's where the value is."

### Wikilinks and why Obsidian

Output is markdown with YAML frontmatter and `[[Concept Name]]` links. That's
Obsidian's format, so you get graph view, backlinks and search for free instead
of building a frontend. The files stay useful even without the tool.

---

## 4. Retrieval, the RAG half

This is the part worth understanding deeply, because it's the most transferable.

### The problem RAG solves

An LLM doesn't know your lecture notes and its context window can't hold a whole
semester. **Retrieval-Augmented Generation** means: find the few most relevant
chunks first, put only those in the prompt, and let the model answer from them.
You get grounded, citable answers instead of plausible guesses.

### Chunking

You don't embed whole documents, because a 40-slide lecture averaged into one
vector means nothing. Notes are split on `## ` headings, so each chunk is one
coherent section.

A bug worth remembering: notes using only `#` headings produced **zero** chunks
and were invisible to search. Four notes were silently missing. The fix recovered
48 chunks. **Chunking bugs don't crash, they just quietly lose data.**

### Embeddings and cosine similarity

An **embedding** maps text to a vector of numbers positioned so that similar
meanings land near each other. Similarity is measured by the **cosine** of the
angle between two vectors: 1.0 is identical direction, 0 is unrelated.

The implementation trick ([store.py:160](../canvas_vault/store.py:160)): store
every vector **L2-normalised** (scaled to length 1). For unit vectors, cosine
similarity *is* the dot product. So the entire search is one matrix multiply,
`mat @ q`.

### Dense vs sparse retrieval

- **Dense** (embeddings) understands paraphrase. "How do you find the combined
  result of two arrows" can match a section on vector addition with no shared
  words.
- **Sparse** (keyword) is precise on exact terms. "L2 regularization" or
  "homework 3" should match literally, and embeddings are surprisingly bad at
  that.

Each is weak where the other is strong, so this uses both.

### BM25

The standard keyword ranking function, about 25 lines in [store.py:41](../canvas_vault/store.py:41).
Three ideas:

1. **Term frequency**: a doc mentioning your word more is more relevant.
2. **Inverse document frequency**: rare words matter more. "regularization"
   discriminates, "the" doesn't.
3. **Length normalisation**: long documents shouldn't win just by being long.

The `k1=1.5` parameter saturates term frequency (the 10th mention adds less than
the 2nd) and `b=0.75` controls how hard length is penalised. Those are the
conventional defaults.

### Reciprocal Rank Fusion

Now you have two ranked lists with **incomparable scores**. Cosine similarity is
0 to 1; BM25 is unbounded. You can't average them.

RRF sidesteps the problem by **throwing the scores away and using only the
ranks**:

```
score(doc) = sum over lists of  1 / (k + rank)      with k = 60
```

Something ranked well by both retrievers beats something ranked first by one and
missing from the other. The `k=60` constant damps the top of each list so rank 1
doesn't dominate. It's from the original 2009 paper and is a widely used default.

**Out loud:** "Fusing two retrievers is awkward because their scores aren't on
the same scale. RRF fixes that by only using rank position, so you never have to
normalise anything."

### Static embeddings

The default embedder is **model2vec** (`potion-base-8M`). A normal sentence
transformer runs a neural network on every query. A static model distills that
network down to a lookup table: each token has a precomputed vector and you
average them.

Tradeoff, measured: install drops from 1.3 GB to 170 MB and PyTorch disappears
entirely, at a cost of MRR 0.90 to 0.85 on the hand set. Adding BM25 recovered
most of it. If `sentence-transformers` happens to be installed, it's used
automatically.

**Out loud:** "Static embeddings gave up a small amount of ranking quality for
an 8x smaller install. I only knew the size of the tradeoff because the eval set
existed first."

### The vector store

SQLite plus numpy, no vector database ([store.py](../canvas_vault/store.py)).
1,385 chunks of float32 is about 2 MB, which fits in CPU cache. Brute-force dot
product over the whole matrix is a fraction of a millisecond.

ChromaDB was there first and got removed. It's built to scale to millions of
vectors and pays for that with a heavy dependency tree. **Approximate nearest
neighbour indexes (HNSW, IVF) exist to avoid scanning everything. At 1,385 rows,
scanning everything is faster than the index lookup would be.**

One safeguard: the store records which model produced its vectors. Vectors from
two different models aren't comparable, so if the embedder changes the index is
dropped and rebuilt rather than silently mixing them.

**Out loud:** "I replaced a vector database with 160 lines of SQLite and numpy.
At this scale brute force wins, and it removed most of the dependency tree."

### Incremental indexing

Re-embedding everything on every sync would be slow and pointless. The indexer
diffs current chunks against stored ones and only touches what changed.

### Intent routing

Not every question should go through RAG. "What's due Friday?" is answered by a
**live API call**, never the index, because a stale index answering a deadline
question is the one failure with real consequences. Conceptual questions go
through retrieval.

**Out loud:** "Deterministic questions get deterministic answers. RAG is for
questions where the answer is buried in prose, not for facts you can look up."

---

## 5. MCP

**Model Context Protocol** is a standard for exposing tools and data to an LLM
client. You write a server declaring some functions; Claude Desktop, Claude Code,
Cursor and Gemini CLI can all call them.

Mechanically: the client launches your process and they exchange **JSON-RPC**
messages over **stdin/stdout**. That has one sharp consequence, and it bit this
project: **stdout is the protocol channel.** A stray `print()` corrupts the
message stream. All logging is forced to stderr.

Two other things learned the hard way:

- Messages have a size limit. An 800 KB chunk exceeded it and broke the
  transport, so results are capped at 2,000 characters.
- The client launches you from an arbitrary working directory, so the server
  `chdir`s to the repo root before touching any relative path.

The architectural payoff: no chat UI to build, no API bill. The subscription you
already pay for becomes the interface.

**Out loud:** "MCP meant I didn't build a frontend or pay for inference. I wrote
a server exposing seven tools and my existing Claude subscription talks to my
notes directly."

---

## 6. Evaluation

The part most side projects skip, and the strongest thing to talk about.

### recall@k and MRR

- **recall@k**: did the right document appear anywhere in the top k? Binary per
  query, averaged. Answers "did we find it at all."
- **MRR** (Mean Reciprocal Rank): `1/rank` of the first correct hit, averaged.
  Rank 1 scores 1.0, rank 5 scores 0.2. Answers "did we find it *first*."

You want both. Recall 1.0 with MRR 0.3 means the answer is always there but
buried.

### Gold sets, and why synthetic ones

A **gold set** is queries paired with their correct answers. 10 hand-written
queries is a smoke test, not an instrument: one query shifting one rank moves MRR
by 0.10, so you can't tune anything against it.

So `tools/make_eval_set.py` generates 70. The trap is **vocabulary leakage**:
show a model a chunk and ask "what question does this answer?" and the question
comes back wearing the chunk's own words, which makes retrieval (especially
BM25) look far better than it is. Every candidate is scored on how much of its
vocabulary already appears in the target chunk, and anything above 0.6 is
discarded. Mean overlap ended up at 0.15.

That gap is real and measured: **1.00 recall on the hand set, 0.90 on the
synthetic one, and 0.88 on its hardest third.** The hand set was written by
someone who already knew the corpus.

One trap inside the trap: the first synthetic set reported a mean overlap of
0.15, which looked excellent. It was flattered by junk. Half that corpus was
base64 image data, and a question invented from gibberish shares no vocabulary
with it. Cleaning the corpus moved overlap to a more honest 0.36. **A metric can
look good because the data is broken.**

### LLM-as-judge

For fuzzy relevance, use a model as the grader. Here it's a cross check, not the
primary instrument, because the deterministic scorer agrees within 5 points and
costs nothing.

### Wilson confidence intervals

10/10 and 100/100 are both "100%" and are not the same claim. A **Wilson score
interval** gives the range the true rate plausibly falls in. 10/10 carries a 95%
interval of 72 to 100%. It behaves properly near 0 and 1, where the textbook
normal approximation produces nonsense like intervals above 100%.

**Out loud:** "Any accuracy number from a small sample needs an error bar. Ten
out of ten sounds perfect and is consistent with a true rate of 72%."

---

## 7. Engineering practices worth naming

**Fault isolation** (§1), **idempotency** (§2), and:

- **TTL caching** on API calls, because Canvas data changes hourly but a single
  sync hits the same endpoint repeatedly.
- **Frozen dataclasses** for `Course`, so identity objects can't be mutated by
  accident. Two bugs came from trying to stash state on one.
- **Write-then-purge ordering.** The vault rebuild used to delete stale notes
  *before* writing replacements, so any mid-write error left the graph destroyed
  and unwritten. Now it writes first and purges after.
- **Test doubles.** The pipeline talks to a live LMS and a paid vision model,
  which made most of it untestable. `tests/conftest.py` provides fake Canvas
  objects and a deterministic hash-based embedder, so the whole pipeline runs
  offline in CI. Two fake courses on purpose, one technical and one humanities,
  so the extraction prompt doesn't quietly assume every class looks like ML.
- **launchd** for scheduling on macOS (the systemd equivalent).

---

## 8. The bugs to tell stories about

Interviewers remember these more than the architecture.

**Silence read as absence.** The MCP `refresh` tool took a fast path that never
listed files, then reported "no changes since last sync." Asked whether a lecture
had been posted, the assistant said no. It had been posted. The model reasoned
correctly from a tool that made *failing to look* indistinguishable from
*looking and finding nothing*. Fixed by making the cheap path still list files
and having the summary state what it actually examined.

**The same shape, twice more.** The judge counted failed API calls as retrieval
misses, reporting 37% recall that was really an outage. And a sync that hit a 403
saved an empty result as "seen", wiping the state so the next healthy run
re-reported the entire term as new.

**The 14% that was arithmetic.** The scorer looped over 10 hand-written pairs and
divided by 70 synthetic ones. A perfect run printed "10/70, recall 14%." That
number got investigated as a retrieval problem, concluded the metric was invalid,
and motivated an LLM judge that then burned three runs against a daily quota.
Corrected, it reads 0.60 and agrees with the judge. **A plausible story explained
a wrong number, and the story was believed for two days.**

**Slug split-brain.** Two functions derived a course's short name differently, so
one half of the code wrote to `vault/DS4400/` while the other linked
`[[DS/Dashboard]]`. Now there's exactly one definition and a test asserting both
callers agree.

The connecting thread: **most of these failures produced a confident wrong answer
rather than an error.** That's the class of bug worth designing against.

---

## Quick answers to likely questions

**Why not just paste slides into ChatGPT?** Doesn't scale past one lecture,
nothing accumulates, and no cross-lecture linking.

**Why not a vector database?** 1,385 chunks. Brute force is faster than the index
lookup and removes most of the dependency tree.

**Why not fine-tune?** This is a search problem. A fine-tuned model would
memorise one student's corpus, need retraining weekly, and lose citations.

**How do you know it works?** recall@5 of 0.90 and MRR 0.69 on 100 synthetic
queries with pooled relevance labels, holding at 0.88 on the hardest third. The
concept graph is scored separately and currently fails 4 of 6 link tests, which
is the top open bug.

**What would you do next?** Fix the concept-link regression, then tune the RRF
fusion weights, which were never tuned because until now no gold set was big
enough to tune against without overfitting.
