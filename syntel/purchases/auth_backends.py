"""بک‌اند احراز هویت با جستجوی غیرحساس به حروف بزرگ و کوچک برای نام کاربری."""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class CaseInsensitiveModelBackend(ModelBackend):
    """احراز هویت که در نام کاربری تفاوتی بین حروف بزرگ و کوچک قائل نمی‌شود."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None

        lookup = str(username).strip()
        if not lookup:
            UserModel().set_password(password)
            return None

        manager = UserModel._default_manager
        exact_user = manager.filter(**{UserModel.USERNAME_FIELD: lookup}).first()
        if exact_user is not None:
            if exact_user.check_password(password) and self.user_can_authenticate(exact_user):
                return exact_user
            return None

        candidates = list(
            manager.filter(**{f'{UserModel.USERNAME_FIELD}__iexact': lookup}).order_by('pk')
        )
        if not candidates:
            UserModel().set_password(password)
            return None

        for user in candidates:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
        return None
