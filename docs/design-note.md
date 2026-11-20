# Chat Agent: design note

## State and workflow

- PostgreSQL stores chats, messages, current facts, proposals, preferences and traces. States include collecting, searching, awaiting approval, executing, completed, limited, error and needs attention.
- The AI extracts details once per message. LiteLLM routes AI calls; Python validates facts, chooses tools and preserves unrelated facts when one detail changes. Providers and payments are mocks; live language understanding uses Groq. Tests use scripted or mocked models offline.

## Approval

- Booking, cancellation and rescheduling need explicit confirmation of a saved proposal ID and fingerprint: a hash of the exact details.
- Provider, slot or price changes invalidate approval. Cancellation/reschedule retries recheck current details before another write. Reviews are untrusted data and cannot approve actions.

## Duplicate prevention and recovery

- The proposal ID is reused as the retry key. Database constraints prevent duplicate booking writes. A short row lock claims execution; the transaction ends before the provider call. A temporary claim prevents overlapping runs from executing the same action.
- After an uncertain booking response, read authoritative status before retrying or claiming success. Cancellation/reschedule recovery verifies saved operation results first. Our own committed change must not be mistaken for an external change after a lost response.

## Memory and context

- Save explicit preferences; let users inspect, correct and forget them. Exclude unconfirmed inferred facts, old messages and reviews from model input. This is simple but limits free-form recall.
- Count message text with the model tokenizer, reserve hosted-format overhead and output space, then drop optional context or reject oversized input. Tests seed 55+ memories and 120 old messages.

## Traces, cost and limits

- Record state changes and model/tool starts, results, errors, duration, sanitized arguments/results and reported usage. Hide credentials in trace copies without changing execution data.
- LiteLLM estimates USD cost from normalized usage and bundled model prices, including cached input; missing usage/pricing stays unknown.
- Web chat awaits pooled AI HTTP under ASGI. Database/approval stages stay synchronous outside the event loop. Bound model/tool calls and cancel AI waits at the remaining 25-second budget. Gunicorn watches worker heartbeats; it can replace a blocked loop, not impose a deadline on a healthy request or force-stop sync code in a thread.

## Actual timeout-after-success trace

- Explicit approval -> status not found -> book commits, then raises TimeoutError -> status confirmed -> completed.
- The authoritative check finds the original booking, so recovery does not book again. See the [raw trace](timeout-after-success.json) and [annotated evidence](timeout-trace.md).
- Celery Beat checks provider cancellations every 10 seconds. A saved follow-up and row lock prevent duplicate notices/proposals. Reuse the original request and current explicit preferences, exclude the cancelled provider, then ask for fresh approval. User cancellations do not trigger this flow. Local mock holds, messages and proposals commit together; failed checks retry on the next tick.
