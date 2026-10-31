from unfold.forms import AdminPasswordChangeForm as UnfoldAdminPasswordChangeForm
from unfold.forms import UserChangeForm as UnfoldUserChangeForm
from unfold.forms import UserCreationForm as UnfoldUserCreationForm

from .models import User


class UserCreationForm(UnfoldUserCreationForm):
    """Create a user from an email and a password."""

    class Meta(UnfoldUserCreationForm.Meta):
        model = User
        fields = ('email',)
        # Django's base form maps "username" to UsernameField; accounts.User has none.
        field_classes = {}


class UserChangeForm(UnfoldUserChangeForm):
    """Edit every field of a user; the password is shown as a read-only hash."""

    class Meta(UnfoldUserChangeForm.Meta):
        model = User
        fields = '__all__'
        field_classes = {}


class AdminPasswordChangeForm(UnfoldAdminPasswordChangeForm):
    """Set a user's password from the admin (Unfold's form is already model-agnostic)."""
