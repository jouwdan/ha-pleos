"""Provider boundaries use synthetic responses and never contact a service."""

import asyncio
import traceback
import unittest
from unittest.mock import AsyncMock, MagicMock

from aiohttp import ClientError, ContentTypeError

from nominatim_reverse_client import Address, GeocodingError, NominatimClient, reverse_url


class ReverseClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = MagicMock()
        self.client = NominatimClient(
            self.session, "https://geo.example/api", user_agent="SyntheticClient/1.0"
        )

    def response(self, payload, status=200):
        response = MagicMock(status=status)
        response.json = AsyncMock(return_value=payload)
        self.session.get.return_value.__aenter__.return_value = response
        return response

    def test_valid_provider_urls_are_normalized(self):
        for value, expected in (
            ("  https://geo.example///  ", "https://geo.example/reverse"),
            ("https://geo.example/api", "https://geo.example/api/reverse"),
            ("https://geo.example/api/reverse/", "https://geo.example/api/reverse"),
            ("http://127.0.0.1:8080", "http://127.0.0.1:8080/reverse"),
            ("http://[::1]:8080", "http://[::1]:8080/reverse"),
        ):
            with self.subTest(value=value):
                self.assertEqual(reverse_url(value), expected)

    def test_unsafe_or_missing_urls_are_rejected(self):
        for value in (
            None,
            12,
            "",
            "  ",
            "ftp://geo.example",
            "https://",
            "https://[invalid",
            "https://geo.example:invalid",
            "https://geo.example:65536",
            "https://user@geo.example",
            "https://user:secret@geo.example",
            "https://:secret@geo.example",
            "https://geo.example?key=secret",
            "https://geo.example/#secret",
            "https://geo.example?",
            "https://geo.example#",
        ):
            with self.subTest(value=value):
                self.assertEqual(reverse_url(value), "")

    async def test_no_provider_means_no_request(self):
        self.client = NominatimClient(self.session, "", user_agent="SyntheticClient/1.0")
        with self.assertRaises(GeocodingError) as caught:
            await self.client.reverse(55.1, 10.1)
        self.assertEqual(caught.exception.code, "service_url_missing")
        self.session.get.assert_not_called()

    async def test_lookup_preserves_address_and_caller_session(self):
        address = "Synthetic Street, " * 30
        self.response({"display_name": "  " + address + "  ", "licence": " Fixture licence "})
        result = await self.client.reverse(55.1234, 10.4321)
        self.assertEqual(result, Address(address.strip(), "Fixture licence"))
        request = self.session.get.call_args
        self.assertEqual(request.args, ("https://geo.example/api/reverse",))
        self.assertEqual(
            request.kwargs["params"],
            {
                "lat": 55.1234,
                "lon": 10.4321,
                "format": "json",
                "addressdetails": 1,
            },
        )
        self.assertEqual(
            request.kwargs["headers"],
            {
                "User-Agent": "SyntheticClient/1.0",
                "Accept": "application/json",
            },
        )
        self.assertEqual(request.kwargs["timeout"].total, 10)
        self.assertIs(request.kwargs["allow_redirects"], False)
        self.session.close.assert_not_called()

    async def test_explicit_api_key_is_trimmed_and_passed_only_in_query(self):
        self.client = NominatimClient(
            self.session, "https://geo.example", user_agent="Fixture", api_key=" fixture-key "
        )
        self.response({"display_name": "Fixture address"})
        await self.client.reverse(0, 0)
        request = self.session.get.call_args
        self.assertEqual(request.kwargs["params"]["key"], "fixture-key")
        self.assertNotIn("fixture-key", repr(request.args))
        self.assertNotIn("fixture-key", repr(request.kwargs["headers"]))

    async def test_attribution_keys_and_optional_values(self):
        for attributes, expected in (
            ({"attribution": " Provider ", "licence": "Other"}, "Provider"),
            ({"licence": " Provider licence "}, "Provider licence"),
            ({"license": " Provider license "}, "Provider license"),
            ({"attribution": "", "licence": "Fallback"}, "Fallback"),
            ({"attribution": " "}, None),
            ({"attribution": 123}, None),
            ({}, None),
        ):
            with self.subTest(attributes=attributes):
                self.response({"display_name": "Fixture", **attributes})
                self.assertEqual((await self.client.reverse(0, 0)).attribution, expected)

    async def test_coordinate_boundaries_are_valid(self):
        self.response({"display_name": "Fixture"})
        for coordinates in ((-90, -180), (90, 180), (0, 0)):
            with self.subTest(coordinates=coordinates):
                self.assertEqual(await self.client.reverse(*coordinates), Address("Fixture"))

    async def test_invalid_coordinates_never_leave_the_process(self):
        for coordinates in (
            (None, 0),
            ("55.1", 0),
            (False, 0),
            (0, True),
            ([], 0),
            (float("nan"), 0),
            (0, float("inf")),
            (float("-inf"), 0),
            (-90.1, 0),
            (90.1, 0),
            (0, -180.1),
            (0, 180.1),
            (10**1000, 0),
        ):
            with self.subTest(coordinates=coordinates):
                with self.assertRaises(GeocodingError) as caught:
                    await self.client.reverse(*coordinates)
                self.assertEqual(caught.exception.code, "invalid_coordinates")
        self.session.get.assert_not_called()

    async def test_non_200_responses_never_parse_body_or_retry(self):
        for status in (301, 302, 401, 403, 429, 500):
            with self.subTest(status=status):
                self.session.get.reset_mock()
                response = self.response({"private": "fixture-secret"}, status)
                with self.assertRaises(GeocodingError) as caught:
                    await self.client.reverse(55.1, 10.1)
                self.assertEqual(str(caught.exception), f"http_{status}")
                self.session.get.assert_called_once()
                response.json.assert_not_awaited()

    async def test_request_errors_are_sanitized_and_not_logged(self):
        secret = "https://geo.example?key=fixture-secret&lat=55.123"
        for error, code in (
            (TimeoutError(secret), "timeout"),
            (ClientError(secret), "connection_error"),
        ):
            with self.subTest(code=code):
                self.session.get.side_effect = error
                with self.assertNoLogs("nominatim_reverse_client", level="DEBUG"):
                    with self.assertRaises(GeocodingError) as caught:
                        await self.client.reverse(55.123, 10.1)
                self.assertEqual(caught.exception.code, code)
                rendered = "".join(traceback.format_exception(caught.exception))
                self.assertNotIn(secret, rendered)
                self.assertNotIn("fixture-secret", rendered)
                self.assertTrue(caught.exception.__suppress_context__)

    async def test_invalid_json_and_content_type_are_sanitized(self):
        for error in (
            ValueError("fixture-private-body"),
            ContentTypeError(request_info=MagicMock(), history=(), message="fixture-private-body"),
        ):
            with self.subTest(error=type(error).__name__):
                self.response({}).json.side_effect = error
                with self.assertRaises(GeocodingError) as caught:
                    await self.client.reverse(55.1, 10.1)
                self.assertEqual(str(caught.exception), "invalid_json")
                self.assertTrue(caught.exception.__suppress_context__)

    async def test_missing_or_malformed_address_is_not_returned(self):
        for payload, code in (
            (None, "invalid_response"),
            ([], "invalid_response"),
            ({}, "no_display_name"),
            ({"display_name": 123}, "no_display_name"),
            ({"display_name": "  "}, "no_display_name"),
            ({"error": "fixture-private-error"}, "no_display_name"),
        ):
            with self.subTest(payload=payload):
                self.response(payload)
                with self.assertRaises(GeocodingError) as caught:
                    await self.client.reverse(55.1, 10.1)
                self.assertEqual(str(caught.exception), code)

    async def test_cancellation_propagates(self):
        self.session.get.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.client.reverse(55.1, 10.1)
        self.session.get.assert_called_once()
