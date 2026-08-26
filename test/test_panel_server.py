import unittest

from x2_operator_panel.panel_server import (
    PanelApplication,
    _build_lan_tls_context,
    _is_loopback_host,
    _is_rfc1918_ipv4_host,
    _parse_lan_allowed_subnet,
    _validate_bind_address,
)


class PanelServerPolicyTest(unittest.TestCase):
    def test_identifies_loopback_and_private_robot_addresses(self):
        self.assertTrue(_is_loopback_host("127.0.0.1"))
        self.assertTrue(_is_loopback_host("::1"))
        self.assertTrue(_is_loopback_host("localhost"))
        self.assertFalse(_is_loopback_host("0.0.0.0"))
        self.assertFalse(_is_loopback_host("192.0.2.10"))
        self.assertTrue(_is_rfc1918_ipv4_host("192.168.252.14"))
        self.assertTrue(_is_rfc1918_ipv4_host("10.2.3.4"))
        self.assertFalse(_is_rfc1918_ipv4_host("0.0.0.0"))
        self.assertFalse(_is_rfc1918_ipv4_host("192.0.2.10"))
        self.assertFalse(_is_rfc1918_ipv4_host("robot.local"))

    def test_lan_binding_requires_explicit_private_robot_address(self):
        _validate_bind_address("127.0.0.1", False)
        _validate_bind_address("192.168.252.14", True)

        with self.assertRaisesRegex(ValueError, "allow_lan_access"):
            _validate_bind_address("192.168.252.14", False)
        with self.assertRaisesRegex(ValueError, "RFC1918"):
            _validate_bind_address("0.0.0.0", True)

    def test_non_loopback_origin_requires_https(self):
        application = object.__new__(PanelApplication)
        application.http_port = 8080
        application.bind_address = "127.0.0.1"
        application.allow_lan_access = False

        with self.assertRaisesRegex(ValueError, "HTTPS"):
            application._build_allowed_origins("http://robot.example")

        origins, secure = application._build_allowed_origins("https://robot.example")
        self.assertEqual(origins, {"https://robot.example"})
        self.assertTrue(secure)

    def test_lan_mode_limits_tls_origin_to_the_robot_address(self):
        application = object.__new__(PanelApplication)
        application.http_port = 8080
        application.bind_address = "192.168.252.14"
        application.allow_lan_access = True

        origins, secure = application._build_allowed_origins("")
        self.assertEqual(origins, {"https://192.168.252.14:8080"})
        self.assertTrue(secure)

        with self.assertRaisesRegex(ValueError, "exactly match"):
            application._build_allowed_origins("https://robot.example")

    def test_lan_mode_requires_private_subnet_and_tls_material(self):
        subnet = _parse_lan_allowed_subnet("192.168.252.0/24", "192.168.252.14")
        self.assertEqual(str(subnet), "192.168.252.0/24")

        with self.assertRaisesRegex(ValueError, "required"):
            _parse_lan_allowed_subnet("", "192.168.252.14")
        with self.assertRaisesRegex(ValueError, "contain"):
            _parse_lan_allowed_subnet("192.168.253.0/24", "192.168.252.14")
        with self.assertRaisesRegex(ValueError, "required"):
            _build_lan_tls_context("", "", True)
        self.assertIsNone(_build_lan_tls_context("", "", False))

    def test_lan_source_address_must_be_in_the_configured_subnet(self):
        application = object.__new__(PanelApplication)
        application.lan_allowed_subnet = _parse_lan_allowed_subnet(
            "192.168.252.0/24", "192.168.252.14"
        )

        self.assertTrue(application.source_address_is_allowed("192.168.252.25"))
        self.assertFalse(application.source_address_is_allowed("192.168.253.25"))
        self.assertFalse(application.source_address_is_allowed(None))

    def test_origins_cannot_mix_transport_security(self):
        application = object.__new__(PanelApplication)
        application.http_port = 8080
        application.bind_address = "127.0.0.1"
        application.allow_lan_access = False

        with self.assertRaisesRegex(ValueError, "mix"):
            application._build_allowed_origins(
                "http://localhost:8080,https://robot.example"
            )


if __name__ == "__main__":
    unittest.main()
