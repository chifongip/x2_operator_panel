import unittest

from x2_operator_panel.panel_server import PanelApplication, _is_loopback_host


class PanelServerPolicyTest(unittest.TestCase):
    def test_only_loopback_bind_names_are_accepted(self):
        self.assertTrue(_is_loopback_host("127.0.0.1"))
        self.assertTrue(_is_loopback_host("::1"))
        self.assertTrue(_is_loopback_host("localhost"))
        self.assertFalse(_is_loopback_host("0.0.0.0"))
        self.assertFalse(_is_loopback_host("192.0.2.10"))

    def test_non_loopback_origin_requires_https(self):
        application = object.__new__(PanelApplication)
        application.http_port = 8080

        with self.assertRaisesRegex(ValueError, "HTTPS"):
            application._build_allowed_origins("http://robot.example")

        origins, secure = application._build_allowed_origins("https://robot.example")
        self.assertEqual(origins, {"https://robot.example"})
        self.assertTrue(secure)

    def test_origins_cannot_mix_transport_security(self):
        application = object.__new__(PanelApplication)
        application.http_port = 8080

        with self.assertRaisesRegex(ValueError, "mix"):
            application._build_allowed_origins(
                "http://localhost:8080,https://robot.example"
            )


if __name__ == "__main__":
    unittest.main()
