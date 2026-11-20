# Chat Agent

Chat Agent helps a user arrange a home service, such as a plumber visit.

- The user explains what they need in chat.
- The AI reads the request and extracts details.
- Python code checks the rules and chooses tools.
- The user reviews a proposal and confirms it.
- The app checks the result before saying a booking succeeded.

All providers and bookings are local mocks. No real service is booked and no payment is taken. Live language understanding uses Groq; offline tests use scripted or mocked model responses.

## Read in this order

1. [Run the project](setup.md): start the app and try a booking.
2. [Understand the code](developer-guide.md): learn where each part lives.
3. [Agent flow](agent-flow.md): follow a request from chat to confirmation.
4. [One-page design note](design-note.md): the short explanation required by the assignment.
5. [Three bugs](bugs.md), [evaluations](eval-results.md) and [timeout evidence](timeout-trace.md): see how behaviour was checked.
6. [Agent collaboration transcript](transcript.md): read the development discussion.

## Demo and runtime notes

- Provider cancellations are checked in the background; replacements require fresh approval. See the [demo steps](setup.md#6-try-proactive-cancellation).
- Gunicorn can stop a stuck worker, but the app does not guarantee an exact 25-second deadline.
- [Watch the narrated demo on YouTube](https://youtu.be/dXzXla9keik).
- [Browse the source on GitHub](https://github.com/harshshahdev111-cell/wrxtcchtdg).
