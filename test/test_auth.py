import unittest

from x2_operator_panel.auth import (
    LoginAttemptLimiter,
    SessionStore,
    create_password_hash,
    verify_password,
)


class AuthTest(unittest.TestCase):
    def test_password_hash_verifies_and_rejects_wrong_password(self):
        encoded = create_password_hash("correct battery staple")

        self.assertTrue(verify_password("correct battery staple", encoded))
        self.assertFalse(verify_password("wrong password", encoded))

    def test_new_login_replaces_the_existing_operator_session(self):
        sessions = SessionStore(create_password_hash("operator"), ttl_sec=60.0)
        first = sessions.login("operator")
        second = sessions.login("operator")

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertFalse(sessions.valid(first.token))
        self.assertTrue(sessions.valid(second.token))

    def test_login_attempts_are_limited_by_source(self):
        limiter = LoginAttemptLimiter(
            per_source_limit=1, global_limit=10, window_sec=60.0
        )

        self.assertEqual(limiter.consume("local"), (True, 0))
        allowed, retry_after = limiter.consume("local")

        self.assertFalse(allowed)
        self.assertGreaterEqual(retry_after, 1)

    def test_login_attempts_have_a_global_limit(self):
        limiter = LoginAttemptLimiter(
            per_source_limit=10, global_limit=1, window_sec=60.0
        )

        self.assertEqual(limiter.consume("source-a"), (True, 0))
        self.assertFalse(limiter.consume("source-b")[0])

    def test_password_hash_rejects_excessive_work_factor(self):
        encoded = create_password_hash("operator")
        _, _, salt, digest = encoded.split("$")

        self.assertFalse(
            verify_password("operator", f"pbkdf2_sha256$2000001${salt}${digest}")
        )

    def test_session_ttl_must_be_positive(self):
        with self.assertRaisesRegex(ValueError, "TTL"):
            SessionStore(create_password_hash("operator"), ttl_sec=0.0)
