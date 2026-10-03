"""Offline tests for ASN registration country lookup (no external requests)."""
import unittest
from unittest.mock import Mock

import netpulse as np


IP = "57.180.193.62"
ORIGIN = "62.193.180.57.origin.asn.cymru.com"
ASN = "AS16509.asn.cymru.com"


def answer(name, text, **extra):
    return {"Status": 0, "Answer": [{"name": name + ".", "type": 16,
                                    "data": text}], **extra}


class NativeTests(unittest.TestCase):
    def session(self, origin=None, country=None):
        origin = origin or answer(ORIGIN, "16509 | 57.180.0.0/14 | JP | arin | 2022-08-22")
        country = country or answer(ASN, "16509 | US | arin | 2000-05-04 | AMAZON-02")
        session = Mock()
        session.get.side_effect = lambda url, **kw: self.response(
            country if kw["params"]["name"].startswith("AS") else origin)
        return session

    def response(self, payload):
        r = Mock()
        r.json.return_value = payload
        return r

    def test_native(self):
        r = np.check_native(self.session(), IP, "US")
        self.assertEqual((r["status"], r["value"]), ("ok", "原生 IP"))

    def test_prefix_country_is_not_asn_country(self):
        r = np.check_native(self.session(), IP, "JP")
        self.assertEqual(r["status"], "negative")
        self.assertEqual(r["evidence"]["asn_country"], "US")
        self.assertEqual(r["evidence"]["asn"], 16509)

    def test_evidence(self):
        r = np.check_native(self.session(), IP, "JP")
        self.assertEqual(r["evidence"]["source"], "Team Cymru")
        self.assertEqual([q["name"] for q in r["evidence"]["queries"]], [ORIGIN, ASN])

    def test_quoted_segmented_txt(self):
        country = answer(ASN, '"16509 | US | arin | " "2000-05-04 | AMAZON-02"')
        self.assertEqual(np.check_native(self.session(country=country), IP, "US")["status"], "ok")

    def test_ipv6(self):
        ip = "2001:db8::1"
        name = ".".join(reversed(np.ipaddress.ip_address(ip).exploded.replace(":", ""))) + ".origin6.asn.cymru.com"
        origin = answer(name, "16509 | 2001:db8::/32 | JP | arin | 2022-08-22")
        s = self.session(origin=origin)
        self.assertEqual(np.check_native(s, ip, "JP")["status"], "negative")
        self.assertEqual(s.get.call_args_list[0].kwargs["params"]["name"], name)

    def test_http_failure_uses_backup_resolver(self):
        s = self.session()
        normal = s.get.side_effect
        def get(url, **kw):
            if url == np.DOH_RESOLVERS[0]:
                r = Mock()
                r.raise_for_status.side_effect = RuntimeError("HTTP 403")
                return r
            return normal(url, **kw)
        s.get.side_effect = get
        r = np.check_native(s, IP, "JP")
        self.assertEqual(r["status"], "negative")
        self.assertTrue(all(q["resolver"] == np.DOH_RESOLVERS[1] for q in r["evidence"]["queries"]))

    def test_timeout_is_unknown(self):
        s = Mock()
        s.get.side_effect = TimeoutError("timed out")
        r = np.check_native(s, IP, "JP")
        self.assertEqual(r["status"], "unknown")
        self.assertIn("timed out", r["error"])

    def test_bad_dns_or_missing_txt(self):
        for d in [{"Status": 3}, {"Status": 2}, {"Status": 0, "Answer": []},
                  answer(ORIGIN, "16509 | prefix | JP | arin | date", TC=True),
                  answer("wrong.example", "16509 | prefix | JP | arin | date")]:
            with self.subTest(d=d):
                self.assertEqual(np.check_native(self.session(origin=d), IP, "JP")["status"], "unknown")

    def test_country_missing_invalid_or_asn_mismatch(self):
        for text in ["16509 | ZZ | arin | date | name", "16509 |  | arin | date | name",
                     "16509 | USA | arin | date | name", "8075 | US | arin | date | name",
                     "malformed"]:
            with self.subTest(text=text):
                r = np.check_native(self.session(country=answer(ASN, text)), IP, "JP")
                self.assertEqual(r["status"], "unknown")
                self.assertIsNone(r["evidence"]["asn_country"])

    def test_multiple_origin_asns_is_unknown(self):
        origin = answer(ORIGIN, "16509 8075 | 57.180.0.0/14 | JP | arin | date")
        self.assertEqual(np.check_native(self.session(origin=origin), IP, "JP")["status"], "unknown")

    def test_origin_prefix_mismatch_is_unknown(self):
        origin = answer(ORIGIN, "16509 | 192.0.2.0/24 | JP | arin | date")
        self.assertEqual(np.check_native(self.session(origin=origin), IP, "JP")["status"], "unknown")

    def test_conflicting_countries_is_unknown(self):
        d = answer(ASN, "16509 | US | arin | date | name")
        d["Answer"].append({"name": ASN, "type": 16, "data": "16509 | JP | arin | date | name"})
        self.assertEqual(np.check_native(self.session(country=d), IP, "JP")["status"], "unknown")

    def test_missing_input_is_unknown_without_requests(self):
        s = Mock()
        for ip, country in [(None, "JP"), (IP, None), ("invalid", "JP"), (IP, "ZZ")]:
            self.assertEqual(np.check_native(s, ip, country)["status"], "unknown")
        s.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
