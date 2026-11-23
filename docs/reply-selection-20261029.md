# Reply Selection, 2026-10-29

## Findings

The desktop selection logs recorded model requests between about 3 and 10
seconds, with some requests reaching the 12-second callback budget. Those
measurements covered model work, not local candidate retrieval.

The old retrieval passed a complete reply into `search_intent_candidates`, whose
contract is a short, explicit intent query. Sentence queries frequently missed.
It then filled candidates using a fixed sequence of ten common emotions. The
16-candidate limit could exclude notes, confusion, protest, sadness or
encouragement before the selector saw them.

Every-reply mode also substituted the first local candidate when the model
returned a valid `no_match`. That could send an image the model had rejected.
Recent-use exclusion could remove the only relevant image while leaving
unrelated alternatives available.

## Reference Implementation

Studied the local `astrbot_plugin_meme_manager` source:

- `mixins/event_handlers.py::_resp_semantic_llm_impl`: retrieve candidates,
  expose per-image annotations, then let the auxiliary model choose an exact ID.
- `mixins/event_handlers.py::_build_emotion_semantic_query`: preserve the
  assistant's attitude and recipient relationship, rather than echoing the user's
  mood.
- `backend/semantic/query.py::search_memes_multi_pack`: use indexed annotations
  and bounded candidates; reject unavailable results.

That semantic path can require a query-generation request, an embedding request
and a selection request. Copying it directly would add network work and require
an embedding provider. The current plugin retains one official host-gateway
selection request per eligible reply.

## Plugin Changes

Candidate retrieval now uses an in-memory lexical index over the existing
captions, descriptions, visible text, tags and category names. CJK phrases and
whole words are weighted by their frequency in the library. Single-character
labels can still match a sentence without treating every function character in
a caption as a useful signal. This is lexical retrieval, not an embedding model
or a substitute for contextual judgment.

The existing short-intent matches remain available. Additional candidates cover
different categories instead of a fixed emotion list. Only library annotations
are cached; edits change the cache key, and conversation text is not cached.
Disabled images and inactive collections remain excluded.

Every-reply mode keeps recent candidates available and marks them for the
selector, so relevance can take priority over variety. The normal mode retains
its original recent-use exclusions.

The model receives complete annotations, including negative usage restrictions.
Repeated category explanations, default fields and the duplicate current reply
are omitted. A successful response can be `{"choice":1}`. Choice validation
accepts only an integer within the current candidate list; explicit text-only,
serious-task and no-match results remain supported. Strict compatibility with
the previous result schema is retained.

Every eligible reply still attempts selection. Ordinary replies prefer the
closest simple expression, but a valid `no_match` no longer triggers an
unreviewed first-candidate send. Timeout, truncation and invalid-output behavior,
the 4096-token reasoning budget, stale-reply checks and host delivery guards are
preserved.

Diagnostics add input size, prompt-token usage when provided, local preparation
time and registration time, without recording conversation or reasoning text.
No host source, model binding or credentials are changed.

## Offline Validation

`tools/desktop-test/evaluate-selection.py` compares the previous implementation
with the new implementation against the same 190-image bundled library and ten
fixed synthetic replies:

| Check | Before | After |
| --- | ---: | ---: |
| Cases with an expected image in the first 16 candidates | 5/10 | 10/10 |
| Mean request size, characters | 3253 | 3136 |
| Local retrieval on this machine, approximate | 1.4 s | 0.13 s |

The cases cover notes, goodnight, companionship, confusion, thanks, mild
protest, anticipation, sadness, encouragement and friendly laughter.
The report is stored at
`.desktop-build/reply-hook-20261028/context-speed-20261029/offline-evaluation.json`.

These are candidate-recall and local-cost measurements, not final model-choice
accuracy or end-to-end latency measurements. Remote inference time, implicit
meaning without lexical overlap, and incorrect or incomplete image annotations
remain practical limits.

Full plugin regression: 1398 passed, 2 skipped. New cases cover current-candidate
choice bounds, annotation edits, disabled/recent images, complete restrictions
and refusing an arbitrary fallback after `no_match`.

The isolated frozen desktop integration also passed: three completed replies,
two delivered images, and one explicit text-only skip. It used a local synthetic
model through the real host gateway and a real Electron renderer. This validates
the compact result format and delivery transport, not remote-model latency or
semantic accuracy. The user desktop plugin was updated in place; the host binary,
model configuration, business configuration and catalog were preserved, with
`every_reply = true` still set.
