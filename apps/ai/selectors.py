"""Read queries of the ai app, scoped to the user."""

from django.utils import timezone

from .models import MealDraft


def drafts_for(user):
    return MealDraft.objects.filter(user=user)


def draft_for(user, pk) -> MealDraft | None:
    """The user's draft, with expiry applied on read (an open draft past its TTL shows as expired)."""
    draft = drafts_for(user).filter(pk=pk).first()
    if draft is not None and draft.status in MealDraft.OPEN and timezone.now() >= draft.expires_at:
        MealDraft.objects.filter(pk=draft.pk, status__in=MealDraft.OPEN).update(
            status=MealDraft.Status.EXPIRED, updated_at=timezone.now())
        draft.refresh_from_db()
    return draft
