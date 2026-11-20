# Agent collaboration transcript

Edited and paraphrased from the project conversation.

[Download the text version](evidence/agent-collaboration-transcript.txt).

## Choosing the backend and project structure

**Developer:** I will use Python and Django REST Framework. Does this assignment also
need a frontend? Would React or Streamlit make more sense?

**AI assistant:** The assignment needs a chat interface and a demonstration of
the chat alongside its trace. Streamlit is an option for a quick Python UI.
DRF with a simple interface is another option.

**Developer:** Use Django REST Framework. Follow the file structure from my
existing Project 28 as the reference, and start building with that structure.

**AI assistant:** I'll inspect that project and adapt its structure for this app.

**Decision:** The developer selected DRF after discussing interface options.
The final frontend used a simple Django page with browser JavaScript.


## Setting code-quality expectations

**Developer:** Keep the integration code clean and simple. Add docstrings to
every function. Readability matters even if the code becomes a little longer.
Use Black to format the Python code.

**AI assistant:** I'll explain function responsibilities in docstrings, expand
dense logic where that helps, and run formatting and lint checks.

**Developer:** Prompts and exceptions can go into separate files.

**AI assistant:** I'll separate those concerns and update the imports.

**Decision:** Readability, clear responsibilities and consistent formatting were
explicit developer requirements throughout the work.


## Applying the reference integration pattern

**Developer:** Use the vendor factory architecture from the integrations folder
in my reference project.

**AI assistant:** I'll adapt its client/service/factory pattern. The factory
selects the integration, the client handles the provider boundary, and the
service handles the application's response format.

**Decision:** The reference pattern came from the developer. The assistant
adapted it to the model and mock-provider integrations in this project.
This gave integration code a consistent location and responsibility.


## Container setup and removing unnecessary helpers

**Developer:** Dockerizing the project seems better. Set that up. Since we run
the app in Docker, which folders and scripts do we actually need?

**AI assistant:** Docker Compose can hold the startup commands. Some scripts are
development tools for recording, documentation and verification rather than
runtime requirements.

**Developer:** Why use an extra startup script when Docker and Compose can do
that? Explain the normal approaches, then simplify it.

**AI assistant:** I'll put migration and server startup directly in Compose and
remove the redundant entrypoint helper. Useful development tools can remain.

**Decision:** The developer challenged extra infrastructure code. Docker and
Compose became the normal runtime path, with development tools kept separate.


## Challenging overengineering

**Developer:** What have we added that is overkill and could be removed?

**AI assistant:** We can simplify the offline demo parser and avoid repeated
validation. Approval enforcement, idempotency, recovery and evaluations still
need to remain because the assignment requires them.

**Developer:** Go ahead with the simplification.

**Decision:** Simplification focused on optional plumbing. Required booking
correctness and evaluation behaviour remained application responsibilities.


## Booking recovery and database locks

**Developer:** Implement booking timeout recovery and duplicate prevention first.
Could we use select_for_update instead of a Redis lock for that?

**AI assistant:** PostgreSQL supports useful row locks for short execution claims.
The existing SQLite setup does not provide that protection. Locks also need
persistent retry keys and database uniqueness constraints for safe recovery.

**Developer:** Switch to PostgreSQL.

**AI assistant:** I'll update the database and Docker configuration, then use
short transactions to claim an approved operation. After an uncertain booking
response, recovery must query authoritative status before another attempt.

**Decision:** The developer raised row locking and approved PostgreSQL. The
assistant implemented the claim, retry and reconciliation details. A timeout
was treated as an uncertain outcome rather than proof that booking failed.


## Reviewing chat state and worker boundaries

**Developer:** Why not store the chat in a table and pass its chat ID throughout
the conversation? Why would that ID need to go to a separate worker?

**AI assistant:** Saved conversation state lets each run load the current request.
A separate process is relevant when execution must be forcibly stopped; it
is not required merely to keep a conversation ID or remember its facts.

**Developer:** Explain the plan in simpler words. What is the simplest approach
without adding a separate worker for every request?

**Decision:** The developer reviewed persistence and challenged the proposed
execution boundary before accepting additional infrastructure.


## Comparing timeout approaches before implementation

**Developer:** Give me the different approaches we could take.

**AI assistant:** The options include synchronous requests with network timeouts
and Gunicorn protection, async requests with a cooperative deadline, Linux
signal timers, a separate process for each run, or Celery task time limits.
Each has a different complexity and cancellation guarantee.

**Developer:** Use the synchronous approach with network timeouts and Gunicorn
timeout for now.

**AI assistant:** I'll implement that approach and document its limits.

**Decision at this stage:** Prefer the smaller change after comparing options.
This was an earlier decision. The web request path later moved to async,
which changed what Gunicorn's timeout could guarantee.


## Revisiting performance and async

**Developer:** Can async and await help where needed? Give me a plan.
Is blocking the request thread the main issue?

**AI assistant:** Async helps many requests wait for network responses at once.
It does not make the model generate faster. Provider quotas, database access,
CPU and connection limits also affect capacity.

**Developer:** Start introducing async here, and keep the code clean.

**AI assistant:** I'll make the AI network wait asynchronous and share its HTTP
connection pool. Database and approval stages can remain short synchronous
functions outside the event loop. The server also needs the ASGI setup.

**Developer:** Would async solve the strict wall-clock requirement as well?

**AI assistant:** Async cancellation is cooperative. It cannot forcibly stop
arbitrary blocking database or tool code running in a thread.

**Decision:** Adopt async for concurrent network waiting while preserving the
booking rules. The hard deadline limitation remained explicit.


## Using a package for generic model functionality

**Developer:** Which requirements could packages handle so we do not recreate
too much functionality ourselves?

**AI assistant:** Existing packages cover API validation, HTTP pooling, token
counting and scheduling. LiteLLM is an option for model routing and estimated
cost. Booking approval and recovery rules still belong in our application.

**Developer:** Can LiteLLM really handle model/provider switching and cost
calculation? Check that before changing the integration.

**AI assistant:** It provides model routing and cost estimates when usage and
pricing are available. Unknown usage or pricing should remain unknown.

**Developer:** Go ahead with that change.

**Decision:** The assistant introduced LiteLLM as an option. The developer
questioned its fit and approved using it for generic model functionality.
The application retained its prompts, validation and booking rules.


## Reviewing requirements and approval safety

**Developer:** Check progress strictly against the assignment. Explain the plan
before implementing exact approval for booking, cancellation and rescheduling.

**AI assistant:** Each action needs a saved proposal containing the exact material
details. Confirmation is enforced in application code. A changed provider,
slot or price requires another proposal and fresh approval.

**Developer:** Review the evaluation categories again. Have we actually covered
the proactive cancellation requirement?

**AI assistant:** That category was still missing. A provider cancellation must
be detected without a new user message, followed by a notification and a
replacement proposal that requires fresh approval.

**Decision:** The developer repeatedly requested requirement-based reviews and
challenged incomplete coverage. The safety requirements themselves came
from the assignment; the assistant designed and implemented their mechanics.


## Adding background follow-up with a clear purpose

**Developer:** Is proactive cancellation a large change? Explain the plan first.

**AI assistant:** Reuse the existing booking flow. Celery Beat can schedule checks
and a worker can detect simulated provider cancellations. Save a notification,
search using the original request and current preferences, exclude the
cancelled provider, then ask for fresh approval. Persist the follow-up so
repeated checks do not create duplicate messages or proposals.

**Developer:** Implement that plan.

**Decision:** Celery and Beat were introduced for periodic background follow-up,
not to move every chat message into a task queue. The assistant proposed
this implementation and the developer approved it after reviewing the plan.


## Specifying environment settings and dependencies

**Developer:** Split configuration into a settings package with base.py,
development.py, production.py and test.py. Keep the root URLs, ASGI and WSGI
entry points under config.

**AI assistant:** I'll move shared configuration into base.py and update the
entry points and Docker configuration to select the correct settings module.

**Developer:** Do the same for requirements: base.txt, development.txt and
production.txt.

**AI assistant:** Shared packages will be listed once in base.txt. Development
adds formatting and documentation tools. Production adds the server packages
used by Docker. I'll update install commands and check the rebuilt image.

**Decision:** The developer supplied both layouts explicitly. The assistant
performed the refactor and checked the resulting runtime configuration.


## Documentation and verification

**Developer:** Update the docs where needed. Did you rebuild to see the updated pages?

**AI assistant:** I'll explain each settings and requirements file, provide
copyable commands, build the docs and check the served preview.

Verification reported after the settings and requirements refactor:

- 135 tests passed in the Linux Docker suite.

- Black and Ruff checks passed.

- The strict MkDocs build passed.

- Docker loaded production settings with debug disabled.

- Test settings selected the offline model and eager Celery tasks.

These results describe that verification run. They are not a claim that
every future change has already been tested.


## Collaboration summary

The developer directed the technology choice, reference architecture,
readability standards and environment layout; questioned complexity and
worker boundaries; requested comparisons before choosing approaches; and
repeatedly reviewed completion against the assignment.

Prepared on 20 November 2026, India time.
