# Understand the code

## The three main jobs

| Part | Job | Example |
|---|---|---|
| Frontend | Display chat, proposals and activity | The user clicks Confirm |
| Agent service | Apply rules and choose the next step | Check the approved price before booking |
| Integration | Talk to a model or mock provider | Call Groq or read the mock booking ledger |

The AI extracts facts. It cannot approve a booking. Python code owns that decision.

## Where to look

Paths below start at the project root.

| File or folder | What you will find |
|---|---|
| `backend/config/settings/` | Database, model, token, timeout and pricing settings |
| `backend/config/gunicorn.py` | ASGI workers and heartbeat timeout |
| `backend/chat_agent/models.py` | Database tables |
| `backend/chat_agent/api/v1/` | URLs, request validation and API views |
| `backend/chat_agent/services/agent.py` | Main workflow and trace calls |
| `backend/chat_agent/services/approvals.py` | Approval fingerprints and execution ownership |
| `backend/chat_agent/services/booking.py` | Booking retries and status checks |
| `backend/chat_agent/services/booking_change.py` | Cancellation/reschedule checks and recovery |
| `backend/chat_agent/services/follow_up.py` | Provider cancellation detection, notification and replacement |
| `backend/chat_agent/tasks.py` and `backend/config/celery.py` | Celery task and periodic scheduler setup |
| `backend/chat_agent/services/context.py` | Fit current context into the token budget |
| `backend/chat_agent/services/tracing.py` | Hide credentials in trace copies |
| `backend/chat_agent/services/costs.py` | Estimate model cost from reported usage |
| `backend/integrations/factory.py` | Choose a model or mock provider client |
| `backend/integrations/litellm/` | LiteLLM SDK calls, worker HTTP pool and response decoding |
| `backend/integrations/llm/` | Shared prompts, verified token counting, offline plans and validation |
| `backend/integrations/providers/client.py` | Eight mock provider tools |
| `backend/chat_agent/tests/` | Offline scenarios and safety checks |
| `requirements/` | Runtime and development packages |

## Settings and packages

- Put settings shared by all environments in `backend/config/settings/base.py`.
- Put local changes in `development.py`, Docker changes in `production.py`, and test changes in `test.py`.
- `DEBUG` shows detailed error pages. Development can enable it; production always turns it off.
- Add a package to `requirements/base.txt` when the app needs it everywhere.
- Add developer tools to `requirements/development.txt` and Docker server packages to `requirements/production.txt`.
- Both package lists include `base.txt`, so shared packages only need to be listed once.
- After changing production packages, run `docker compose up --build -d --wait` to rebuild the containers.

## Database tables in simple words

| Table | What it remembers |
|---|---|
| `Conversation` | Chat ID, user, workflow state and current request facts |
| `Message` | User and assistant messages linked to a chat |
| `Proposal` | Exact action to approve, fingerprint, expiry and result |
| `Memory` | Saved preferences, including whether they are explicit |
| `Trace` | What happened during a run |
| `MockHold` | A temporary quote and slot hold |
| `MockBooking` | The mock provider's booking records |
| `MockOperation` | Saved cancellation/reschedule results |
| `CancellationFollowUp` | One saved notification and replacement outcome per cancelled booking |

`MockHold.request_details` keeps the original facts and effective constraints before booking. Later chat changes cannot erase the request needed for a replacement. The periodic service reads the mock provider ledger, identifies provider cancellations, then uses the existing search, hold and approval path. It never calls `book` itself. A short transaction locks the follow-up and saves local mock holds, messages, traces and the new proposal together.

The browser sends the same chat ID each turn. A new **run ID** identifies each message or confirmation attempt.

## Words used in the project

- **Proposal:** an action shown to the user before execution.
- **Fingerprint:** a hash of the proposal details. Changed details produce a different hash.
- **Idempotency key:** an operation ID reused on retries so one approved action is not repeated.
- **Execution claim:** temporary ownership of an operation. Another run must not execute it at the same time.
- **Reconciliation:** ask the provider what actually happened after an uncertain response.
- **Invariant:** a rule that must always hold, such as no booking without valid approval.
- **Token:** a unit of model input or output. It is not the same as a word or byte.

## Mock tools

| Tool | What it does |
|---|---|
| `search_providers(service, area, date_range)` | Return three providers, prices, ratings and slots |
| `get_provider(provider_id)` | Return reviews, including a malicious instruction example |
| `hold_slot(provider_id, slot)` | Hold a quote for ten minutes; a scenario can change its price |
| `book(hold_id, idempotency_key)` | Book once, return a conflict or time out after possibly booking |
| `get_booking_status(hold_id or idempotency_key)` | Read the authoritative booking record |
| `cancel(booking_id, idempotency_key)` | First call fails with 503; retry succeeds |
| `reschedule(booking_id, slot, idempotency_key)` | Change a slot or reject a configured conflict |
| `list_bookings(user_id)` | Return the user's saved bookings |

The `random` scenario is seeded: 70% success, 15% slot taken, and 15% ambiguous timeouts. Half of the timeout range commits a booking. These are weighted outcomes across keys, not a guarantee for each small test sample.

For slot conflicts, set `MOCK_CONFLICT_SLOTS` to comma-separated India-local times or exact ISO datetimes. Default `14:00` blocks that time each day only in the `slot_conflict` scenario. Other slots can succeed after approval.

## Memory and context

- Only current, explicit preferences on the allowlist reach the model.
- Old chat text is stored for display but is excluded from prompts.
- This prevents forgotten details in old messages from being sent again.
- The trade-off is less free-form use of old chat history.
- Tests seed 55+ memories and 120 old messages and inspect the outgoing request.
- Message text is counted with the model tokenizer. A separate allowance covers hosted formatting.

## Read traces and cost

- A call first has status `started`, then `success` or `error`.
- `latency_ms` is the measured duration of a completed call.
- A call left at `started` may have been interrupted. It is not proof of success or failure.
- Trace copies hide credentials; execution data is unchanged.
- Ordinary review text remains visible as untrusted evidence.
- Provider-reported usage is saved even when JSON decoding or plan validation fails, when available.
- LiteLLM calculates cost from its bundled price catalog and normalized usage. The trace identifies the source as `litellm`; manual price settings were removed.
- Missing usage or pricing means **unknown**, not free.
- The SDK accounts for cached input and reported completion usage. Our code does not maintain its own pricing arithmetic. Unknown prices remain unknown.

## Async in simple words

- The chat view uses ADRF, a small async adapter for Django REST Framework.
- `Agent.achat()` awaits the AI call. While it waits, other chats can run on the same server worker.
- LiteLLM handles async model calls and provider-specific formats. A small adapter shares a bounded HTTP pool for each ASGI worker.
- `LLM_MAX_CONNECTIONS=50` caps model connections per worker. A one-second pool wait prevents indefinite queuing for a connection.
- Database stages use `sync_to_async` around small synchronous functions. Approval row locks and transactions keep their existing behaviour.
- Local provider mocks have no network wait, so converting every function to async would not make them faster.
- The synchronous entry points remain for offline commands and tests. Web chat uses async.
- BlackNoise serves collected static files at the ASGI boundary; synchronous WhiteNoise middleware is removed from the request path.
- Async lets independent requests overlap while waiting for network responses.

## Time limits

- One model call per message and a configurable tool-call cap.
- Normal agent budget: 25 seconds, checked between sync stages and enforced around awaited model calls.
- Model connection/read limits: up to 5/20 seconds, capped by remaining time.
- Gunicorn watches ASGI worker heartbeats. It can restart a blocked event loop around 30 seconds; a healthy loop with a slow request keeps sending heartbeats.
- The async AI wait can be cancelled. A synchronous function running in a thread cannot be forcibly stopped by that cancellation, so an exact deadline for all code is not claimed. A forced worker restart can drop the connection.

## Make a small change safely

1. Find the service that owns the rule.
2. Keep network code in its integration client.
3. Add a docstring explaining what a new function does.
4. For a safety rule, add a test checking the outcome or invariant.
5. Run Black, Ruff and the relevant tests.
6. Regenerate the evaluation table after changing scenarios.

## Switch the AI provider

1. Set `LLM_MODEL=groq/openai/gpt-oss-20b` for our Groq model, or `LLM_MODEL=openai/gpt-4.1-mini` for OpenAI.
2. Set `LLM_API_KEY` for that provider.
3. Normally leave `LLM_BASE_URL` blank; remove an old provider's override when switching.
4. Restart the backend container.

LiteLLM needs no separate proxy container here. It handles AI providers, not the eight home-service mocks. The app still enforces context budgets with verified tokenizer mappings; unsupported model families need a verified tokenizer before use. SDK retries/fallbacks and optional metadata fetches are disabled. Price information comes from the pinned package's bundled catalog, so missing prices stay unknown.
