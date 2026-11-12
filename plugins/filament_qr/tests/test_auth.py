import unittest

from tests.helpers import fq


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SessionManagerTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.sessions = fq.SessionManager(clock=self.clock)

    def test_correct_pin_yields_valid_session(self):
        token = self.sessions.authenticate(self.sessions.pin, '10.0.0.2')
        self.assertTrue(self.sessions.validate(token))
        self.assertFalse(self.sessions.validate('not-a-token'))
        self.assertFalse(self.sessions.validate(''))

    def test_wrong_pin_is_rejected(self):
        with self.assertRaises(fq.AuthError) as ctx:
            self.sessions.authenticate('bad', '10.0.0.2')
        self.assertEqual(ctx.exception.status, 403)

    def test_failures_are_throttled_per_ip_and_recover(self):
        for _ in range(fq.SessionManager.MAX_FAILURES):
            with self.assertRaises(fq.AuthError):
                self.sessions.authenticate('bad', '10.0.0.2')
        with self.assertRaises(fq.AuthError) as ctx:
            self.sessions.authenticate(self.sessions.pin, '10.0.0.2')
        self.assertEqual(ctx.exception.status, 429)
        self.assertTrue(self.sessions.authenticate(self.sessions.pin, '10.0.0.3'))

        self.clock.now += fq.SessionManager.FAILURE_WINDOW + 1
        self.assertTrue(self.sessions.authenticate(self.sessions.pin, '10.0.0.2'))

    def test_idle_and_absolute_expiry(self):
        token = self.sessions.authenticate(self.sessions.pin, 'ip')
        self.clock.now += fq.SessionManager.IDLE_TIMEOUT + 1
        self.assertFalse(self.sessions.validate(token))

        token = self.sessions.authenticate(self.sessions.pin, 'ip')
        step = fq.SessionManager.IDLE_TIMEOUT / 2
        elapsed = 0.0
        while elapsed <= fq.SessionManager.ABSOLUTE_TIMEOUT - step:
            self.clock.now += step
            elapsed += step
            self.assertTrue(self.sessions.validate(token))
        self.clock.now += step
        self.assertFalse(self.sessions.validate(token))

    def test_revoke_rotate_and_revoke_all(self):
        a = self.sessions.authenticate(self.sessions.pin, 'ip')
        b = self.sessions.authenticate(self.sessions.pin, 'ip')
        self.sessions.revoke(a)
        self.assertFalse(self.sessions.validate(a))
        self.assertTrue(self.sessions.validate(b))

        old_pin = self.sessions.pin
        self.sessions.rotate_pin()
        self.assertFalse(self.sessions.validate(b))
        self.assertEqual(len(self.sessions.pin), 6)
        if self.sessions.pin != old_pin:
            with self.assertRaises(fq.AuthError):
                self.sessions.authenticate(old_pin, 'ip')

        c = self.sessions.authenticate(self.sessions.pin, 'ip')
        self.sessions.revoke_all()
        self.assertFalse(self.sessions.validate(c))
        self.assertEqual(self.sessions.session_count(), 0)

    def test_tokens_are_not_stored_in_plain_text(self):
        token = self.sessions.authenticate(self.sessions.pin, 'ip')
        self.assertNotIn(token, self.sessions._sessions)

    def test_tracked_state_is_bounded(self):
        for i in range(fq.SessionManager.MAX_TRACKED_IPS + 50):
            with self.assertRaises(fq.AuthError):
                self.sessions.authenticate('bad', f'10.1.{i // 250}.{i % 250}')
        self.assertLessEqual(len(self.sessions._failures), fq.SessionManager.MAX_TRACKED_IPS)
        for _ in range(fq.SessionManager.MAX_SESSIONS + 10):
            self.sessions.authenticate(self.sessions.pin, 'ip')
        self.assertLessEqual(self.sessions.session_count(), fq.SessionManager.MAX_SESSIONS)
