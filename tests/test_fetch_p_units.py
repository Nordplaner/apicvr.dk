"""Tests for production-unit fetching.

The bug these guard against: `fetch_p_units` used to issue a single
Elasticsearch request with a hard-coded `"size": 1000`. A `terms` query over
more than 1000 P-numbers still matched every one of them, but the response was
truncated to 1000 hits in the index's own arbitrary order, with nothing to
indicate the list was short. Københavns Kommune (CVR 64942212, 2645 production
units) silently lost 1645 of them.
"""

import json
from unittest.mock import patch

import pytest
import requests

from apis import searchcvr


def make_hit(p_number):
    return {"_source": {"VrproduktionsEnhed": {"pNummer": p_number}}}


def make_response(status_code=200, hits=None, body=None):
    """A stand-in for `requests.Response` covering only what the code reads."""

    class FakeResponse:
        def __init__(self):
            self.status_code = status_code
            self.text = "fake"

        def json(self):
            if body is not None:
                raise ValueError("not json")
            return {"hits": {"hits": hits or []}}

    return FakeResponse()


def payload_of(call):
    """The decoded ES request body for a captured `requests.post` call."""
    return json.loads(call.kwargs["data"])


def elasticsearch(*_args, **kwargs):
    """Stand-in for the CVR index that truncates to `size`, exactly as ES does.

    Honouring `size` is the whole point: a fake that returns every matched
    document regardless would let the original single-request implementation
    pass, since the truncation happened server-side.
    """
    payload = json.loads(kwargs["data"])
    matched = payload["query"]["terms"]["VrproduktionsEnhed.pNummer"]
    return make_response(hits=[make_hit(n) for n in matched[: payload["size"]]])


class TestEmptyInput:
    def test_returns_empty_list_without_calling_elasticsearch(self):
        with patch.object(searchcvr.requests, "post") as post:
            assert searchcvr.fetch_p_units([]) == []
        post.assert_not_called()


class TestSingleBatch:
    def test_returns_every_requested_unit(self):
        p_numbers = [1003, 1001, 1002]
        with patch.object(searchcvr.requests, "post") as post:
            post.return_value = make_response(hits=[make_hit(n) for n in p_numbers])
            result = searchcvr.fetch_p_units(p_numbers)

        assert [unit["p_number"] for unit in result] == [1001, 1002, 1003]
        assert post.call_count == 1

    def test_asks_for_as_many_hits_as_there_are_p_numbers(self):
        p_numbers = [1001, 1002, 1003]
        with patch.object(searchcvr.requests, "post") as post:
            post.return_value = make_response(hits=[make_hit(n) for n in p_numbers])
            searchcvr.fetch_p_units(p_numbers)

        assert payload_of(post.call_args)["size"] == 3


class TestMultipleBatches:
    """The regression case: more P-numbers than fit in one request."""

    def test_returns_all_units_for_a_company_with_2645_p_numbers(self):
        p_numbers = list(range(1_000_000, 1_000_000 + 2645))

        with patch.object(searchcvr.requests, "post", side_effect=elasticsearch) as post:
            result = searchcvr.fetch_p_units(p_numbers)

        assert len(result) == 2645
        assert [unit["p_number"] for unit in result] == p_numbers
        assert post.call_count == 3

    def test_never_requests_fewer_hits_than_the_batch_it_sends(self):
        p_numbers = list(range(1_000_000, 1_000_000 + 2645))

        with patch.object(searchcvr.requests, "post", side_effect=elasticsearch) as post:
            searchcvr.fetch_p_units(p_numbers)

        for call in post.call_args_list:
            payload = payload_of(call)
            sent = payload["query"]["terms"]["VrproduktionsEnhed.pNummer"]
            assert payload["size"] == len(sent)

        assert [len(payload_of(c)["query"]["terms"]["VrproduktionsEnhed.pNummer"]) for c in post.call_args_list] == [1000, 1000, 645]

    def test_batch_boundary_of_exactly_one_batch_stays_a_single_request(self):
        p_numbers = list(range(1_000_000, 1_000_000 + searchcvr.P_UNIT_BATCH_SIZE))
        with patch.object(searchcvr.requests, "post") as post:
            post.return_value = make_response(hits=[make_hit(n) for n in p_numbers])
            result = searchcvr.fetch_p_units(p_numbers)

        assert post.call_count == 1
        assert len(result) == searchcvr.P_UNIT_BATCH_SIZE

    def test_one_over_the_batch_size_splits_into_two_requests(self):
        p_numbers = list(range(1_000_000, 1_000_000 + searchcvr.P_UNIT_BATCH_SIZE + 1))

        with patch.object(searchcvr.requests, "post", side_effect=elasticsearch) as post:
            result = searchcvr.fetch_p_units(p_numbers)

        assert post.call_count == 2
        assert len(result) == searchcvr.P_UNIT_BATCH_SIZE + 1


class TestOrdering:
    def test_sorts_by_p_number_regardless_of_index_order(self):
        with patch.object(searchcvr.requests, "post") as post:
            post.return_value = make_response(hits=[make_hit(n) for n in [1020420517, 1009652740, 1016165073]])
            result = searchcvr.fetch_p_units([1020420517, 1009652740, 1016165073])

        assert [unit["p_number"] for unit in result] == [1009652740, 1016165073, 1020420517]

    def test_sorts_a_unit_with_a_null_p_number_first_instead_of_raising(self):
        # A P-unit document that exists but carries no `pNummer` would make a
        # naive `sort(key=itemgetter("p_number"))` raise on None vs int.
        hits = [make_hit(1002), {"_source": {"VrproduktionsEnhed": {"reklamebeskyttet": False}}}]
        with patch.object(searchcvr.requests, "post") as post:
            post.return_value = make_response(hits=hits)
            result = searchcvr.fetch_p_units([1002, 1003])

        assert [unit["p_number"] for unit in result] == [None, 1002]


class TestFailureIsAllOrNothing:
    """A short list must never be passed off as a complete one."""

    def test_returns_empty_when_a_later_batch_fails(self):
        p_numbers = list(range(1_000_000, 1_000_000 + 1500))
        responses = [
            make_response(hits=[make_hit(n) for n in p_numbers[:1000]]),
            make_response(status_code=500),
        ]

        with patch.object(searchcvr.requests, "post", side_effect=responses):
            assert searchcvr.fetch_p_units(p_numbers) == []

    def test_returns_empty_on_non_200(self):
        with patch.object(searchcvr.requests, "post") as post:
            post.return_value = make_response(status_code=503)
            assert searchcvr.fetch_p_units([1001]) == []

    def test_returns_empty_on_request_exception(self):
        with patch.object(searchcvr.requests, "post", side_effect=requests.RequestException("boom")):
            assert searchcvr.fetch_p_units([1001]) == []

    def test_returns_empty_on_unparseable_body(self):
        with patch.object(searchcvr.requests, "post") as post:
            post.return_value = make_response(body="<html>nope</html>")
            assert searchcvr.fetch_p_units([1001]) == []


class TestMalformedHits:
    def test_skips_hits_without_a_production_unit_source(self):
        hits = [make_hit(1001), {"_source": {}}, {}, make_hit(1002)]
        with patch.object(searchcvr.requests, "post") as post:
            post.return_value = make_response(hits=hits)
            result = searchcvr.fetch_p_units([1001, 1002])

        assert [unit["p_number"] for unit in result] == [1001, 1002]

    def test_returns_empty_when_the_response_has_no_hits_key(self):
        class FakeResponse:
            status_code = 200
            text = "fake"

            def json(self):
                return {}

        with patch.object(searchcvr.requests, "post") as post:
            post.return_value = FakeResponse()
            assert searchcvr.fetch_p_units([1001]) == []
