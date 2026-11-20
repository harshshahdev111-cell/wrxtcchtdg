from .models import Memory

PREFERENCE_KEYS = {"area", "earliest_time", "preferred_provider"}


def current_preferences(user_id):
    """Return only current, explicitly confirmed preferences supported by the agent."""
    return dict(
        Memory.objects.filter(user_id=user_id, explicit=True, key__in=PREFERENCE_KEYS).values_list(
            "key", "value"
        )
    )
