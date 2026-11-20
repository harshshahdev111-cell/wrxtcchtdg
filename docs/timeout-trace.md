# Timeout after success: actual evidence

This is an actual offline run using the local provider mock. The provider saved a booking, then raised a timeout before the agent received its response.

[Download the complete raw JSON trace](timeout-after-success.json).

## What happened

1. A simulated user explicitly confirmed the saved proposal.
2. The first authoritative status check found no booking.
3. The booking tool committed the booking, then raised `TimeoutError`.
4. The next status check found the confirmed booking.
5. The agent verified the provider, slot, price and retry key, then reported completion.

## Recorded confirmation events

| Event | Recorded outcome | Duration (ms) |
|---|---|---|
| `explicit_confirmation` | explicit confirmation saved | 0.00 |
| `get_booking_status` | not_found | 4.91 |
| `book` | TimeoutError | 7.57 |
| `get_booking_status` | confirmed | 3.26 |
| `completed` | Action verified and completed. | 0.00 |

## Why this is safe

- Actual run ID: `b53de831-5a35-4580-84b8-a19e762ac729`.
- Actual proposal / retry key: `a6829e2f-6357-4195-ae59-b7759329e088`.
- Booking tool calls in this confirmation run: **1**.
- A timeout does not prove failure. The status lookup is the source of truth.
- Recovery checks the existing booking instead of placing another one.
- If status were unavailable, the correct outcome would remain unresolved; the user would retry the same confirmation.

## Recreate the evidence

Run from the project root, with PostgreSQL running:

```powershell
.\.venv\Scripts\python backend/manage.py demo_booking --output docs/timeout-after-success.json
```

This command uses a scripted offline model and local mock tools. It creates one new demonstration conversation and booking. No live AI call or real booking is made.
The raw trace records its own timestamps. If you recreate it, refresh this annotated page to match the new run.
