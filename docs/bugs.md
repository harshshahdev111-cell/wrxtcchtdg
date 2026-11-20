# Three bugs and their fixes

These bugs were found while building and reviewing the first implementation. They are not claimed as production incidents.

## 1. A correction could change the action

- **What broke:** after asking to reschedule, a user supplied only a corrected slot. The action could become a new booking.
- **Why:** the plan serializer supplied a default action of `book` when no action was given.
- **Fix:** remove that default and merge only new fields into the saved request.
- **Regression test:** `test_reschedule_correction_preserves_action`.
- **Rule protected:** changing one detail must preserve unrelated context.

## 2. An exact time boundary could be rejected

- **What broke:** an appointment exactly at the earliest allowed time could be filtered out.
- **Why:** one time was formatted as `10:00:00`, another as `10:00`. Comparing those strings gave the wrong boundary result.
- **Fix:** normalize request times to `HH:MM` before comparing them with slot times.
- **Regression tests:** `test_exact_earliest_time_boundary_is_inclusive` and `test_morning_window_excludes_afternoon`.
- **Rule protected:** provider slots must respect the user's time window, including allowed boundaries.

## 3. A held quote could change after the proposal

- **What broke:** the proposal kept its original fingerprint even when the underlying hold's provider, slot or price changed.
- **Why:** checking the proposal hash did not check the current provider record.
- **Fix:** compare the held provider, slot and price with the proposal before booking. Invalidate approval when they differ.
- **Regression tests:** `test_changed_quote_requires_fresh_approval`, `test_changed_slot_requires_fresh_approval` and `test_changed_provider_requires_fresh_approval`.
- **Rule protected:** approval applies only to the exact material details the user reviewed.

## Run the checks

```powershell
docker compose exec -T backend python manage.py test --settings=config.settings.test chat_agent.tests.test_agent --noinput
```

The [evaluation table](eval-results.md) is generated from actual tests. A passing test checks behaviour, not just a particular reply sentence.
