"""Helpers for other apps' tests."""
from unittest import mock


def skip_password_prompt(testcase):
    """For tests that aren't about the first-login password change: every new account is flagged
    (User.must_change_password), so let their requests through instead of redirecting them."""
    from .middleware import PasswordChangeRequiredMiddleware

    patcher = mock.patch.object(PasswordChangeRequiredMiddleware, "__call__",
                                lambda self, request: self.get_response(request))
    patcher.start()
    testcase.addCleanup(patcher.stop)
