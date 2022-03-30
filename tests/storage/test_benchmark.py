# Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
# or more contributor license agreements. Licensed under the Elastic License 2.0;
# you may not use this file except in compliance with the Elastic License 2.0.

import base64
import gzip
import random
import string
from typing import Optional, Union

import orjson
import pytest
import pytest_benchmark.fixture
import simplejson
import ujson

import storage.decorator
from storage import PayloadStorage, StorageReader

_LENGTH_BELOW_THRESHOLD: int = 40
_LENGTH_ABOVE_THRESHOLD: int = 1024 * 10
_LENGTH_1M: int = 1024**2

_IS_PLAIN: str = "_IS_PLAIN"
_IS_JSON: str = "_IS_JSON"
_IS_JSON_LIKE: str = "_IS_JSON_LIKE"


class MockContentBase:
    f_size_gzip: int = 0
    f_size_plain: int = 0
    f_content_gzip: bytes = b""
    f_content_plain: bytes = b""

    mock_content: bytes = b""

    @staticmethod
    def init_content(content_type: str, newline: bytes, length_multiplier: int = _LENGTH_ABOVE_THRESHOLD) -> None:
        if len(newline) == 0:
            if content_type == _IS_JSON:
                mock_content = (
                    b"{"
                    + newline
                    + b'"'
                    + "".join(random.choices(string.ascii_letters + string.digits, k=random.randint(1, 4))).encode(
                        "utf-8"
                    )
                    + b'"'
                    + newline
                    + b":"
                    + newline
                    + b'"'
                    + "".join(random.choices(string.ascii_letters + string.digits, k=random.randint(1, 4))).encode(
                        "utf-8"
                    )
                    + b'"'
                    + newline
                    + b"}"
                )
            else:
                mock_content = "".join(
                    random.choices(string.ascii_letters + string.digits, k=random.randint(1, 20))
                ).encode("utf-8")
        else:
            if content_type == _IS_JSON:
                # every json entry is from 14 to 39 chars, repeated for half of length_multiplier
                mock_content = newline.join(
                    [
                        b"{"
                        + newline
                        + b'"'
                        + "".join(random.choices(string.ascii_letters + string.digits, k=random.randint(1, 5))).encode(
                            "utf-8"
                        )
                        + b'"'
                        + newline
                        + b":"
                        + newline
                        + b'"'
                        + "".join(random.choices(string.ascii_letters + string.digits, k=random.randint(1, 5))).encode(
                            "utf-8"
                        )
                        + b'"'
                        + newline
                        + b"}"
                        + newline
                        for _ in range(1, int(length_multiplier / 2))
                    ]
                )
            else:
                # every line is from 0 to 20 chars, repeated for length_multiplier
                mock_content = newline.join(
                    [
                        "".join(random.choices(string.ascii_letters + string.digits, k=random.randint(0, 20))).encode(
                            "utf-8"
                        )
                        for _ in range(1, length_multiplier)
                    ]
                )

        if content_type == _IS_JSON_LIKE:
            mock_content = b"{" + mock_content

        MockContentBase.mock_content = mock_content

    @staticmethod
    def init_content_for_benchmark() -> None:
        MockContentBase.f_content_gzip = base64.b64encode(gzip.compress(MockContentBase.mock_content))
        MockContentBase.f_content_plain = base64.b64encode(MockContentBase.mock_content)

        MockContentBase.f_size_gzip = len(MockContentBase.f_content_gzip)
        MockContentBase.f_size_plain = len(MockContentBase.f_content_plain)


class Setup:
    content_type: Optional[str] = None
    payload_storage: Optional[PayloadStorage] = None
    original: Optional[bytes] = None
    payload: Optional[str] = None

    @staticmethod
    def setup() -> None:
        if len(MockContentBase.mock_content) == 0:
            MockContentBase.init_content(content_type=_IS_JSON, newline=b"\n", length_multiplier=_LENGTH_1M)
            MockContentBase.init_content_for_benchmark()
            Setup.original = base64.b64decode(MockContentBase.f_content_plain)
            Setup.payload = MockContentBase.f_content_plain.decode("utf-8")


def wrap() -> list[tuple[Union[StorageReader, bytes], int, int, int]]:
    assert Setup.payload is not None
    Setup.payload_storage = PayloadStorage(payload=Setup.payload)
    return list(Setup.payload_storage.get_by_lines(range_start=0))


@pytest.mark.benchmark(group="plain")
def test_json_collector_plain_orjson(benchmark: pytest_benchmark.fixture.BenchmarkFixture) -> None:
    Setup.setup()
    assert Setup.original is not None
    storage.decorator.json_library = orjson
    MockContentBase.f_content_plain = base64.b64encode(Setup.original[1:])
    lines = benchmark.pedantic(wrap, iterations=1, rounds=100)
    original: bytes = base64.b64decode(MockContentBase.f_content_plain)
    original_length: int = len(original)

    assert lines[-1][1] == original_length


@pytest.mark.benchmark(group="json")
def test_json_collector_json_orjson(benchmark: pytest_benchmark.fixture.BenchmarkFixture) -> None:
    Setup.setup()
    assert Setup.original is not None
    storage.decorator.json_library = orjson
    MockContentBase.f_content_plain = base64.b64encode(Setup.original)
    lines = benchmark.pedantic(wrap, iterations=1, rounds=100)
    original: bytes = base64.b64decode(MockContentBase.f_content_plain)
    original_length: int = len(original)

    assert lines[-1][1] == original_length


@pytest.mark.benchmark(group="json like")
def test_json_collector_json_like_orjson(benchmark: pytest_benchmark.fixture.BenchmarkFixture) -> None:
    Setup.setup()
    assert Setup.original is not None
    storage.decorator.json_library = orjson
    MockContentBase.f_content_plain = base64.b64encode(b"{" + Setup.original)
    lines = benchmark.pedantic(wrap, iterations=1, rounds=100)
    original: bytes = base64.b64decode(MockContentBase.f_content_plain)
    original_length: int = len(original)

    assert lines[-1][1] == original_length


@pytest.mark.benchmark(group="plain")
def test_json_collector_plain_simplejson(benchmark: pytest_benchmark.fixture.BenchmarkFixture) -> None:
    Setup.setup()
    assert Setup.original is not None
    storage.decorator.json_library = simplejson
    MockContentBase.f_content_plain = base64.b64encode(Setup.original[1:])
    lines = benchmark.pedantic(wrap, iterations=1, rounds=100)
    original: bytes = base64.b64decode(MockContentBase.f_content_plain)
    original_length: int = len(original)

    assert lines[-1][1] == original_length


@pytest.mark.benchmark(group="json")
def test_json_collector_json_simplejson(benchmark: pytest_benchmark.fixture.BenchmarkFixture) -> None:
    Setup.setup()
    assert Setup.original is not None
    storage.decorator.json_library = simplejson
    MockContentBase.f_content_plain = base64.b64encode(Setup.original)
    lines = benchmark.pedantic(wrap, iterations=1, rounds=100)
    original: bytes = base64.b64decode(MockContentBase.f_content_plain)
    original_length: int = len(original)

    assert lines[-1][1] == original_length


@pytest.mark.benchmark(group="json like")
def test_json_collector_json_like_simplejson(benchmark: pytest_benchmark.fixture.BenchmarkFixture) -> None:
    Setup.setup()
    assert Setup.original is not None
    storage.decorator.json_library = simplejson
    MockContentBase.f_content_plain = base64.b64encode(b"{" + Setup.original)
    lines = benchmark.pedantic(wrap, iterations=1, rounds=100)
    original: bytes = base64.b64decode(MockContentBase.f_content_plain)
    original_length: int = len(original)

    assert lines[-1][1] == original_length


@pytest.mark.benchmark(group="plain")
def test_json_collector_plain_ujson(benchmark: pytest_benchmark.fixture.BenchmarkFixture) -> None:
    Setup.setup()
    assert Setup.original is not None
    storage.decorator.json_library = ujson
    MockContentBase.f_content_plain = base64.b64encode(Setup.original[1:])
    lines = benchmark.pedantic(wrap, iterations=1, rounds=100)
    original: bytes = base64.b64decode(MockContentBase.f_content_plain)
    original_length: int = len(original)

    assert lines[-1][1] == original_length


@pytest.mark.benchmark(group="json")
def test_json_collector_json_ujson(benchmark: pytest_benchmark.fixture.BenchmarkFixture) -> None:
    Setup.setup()
    assert Setup.original is not None
    storage.decorator.json_library = ujson
    MockContentBase.f_content_plain = base64.b64encode(Setup.original)
    lines = benchmark.pedantic(wrap, iterations=1, rounds=100)
    original: bytes = base64.b64decode(MockContentBase.f_content_plain)
    original_length: int = len(original)

    assert lines[-1][1] == original_length


@pytest.mark.benchmark(group="json like")
def test_json_collector_json_like_ujson(benchmark: pytest_benchmark.fixture.BenchmarkFixture) -> None:
    Setup.setup()
    assert Setup.original is not None
    storage.decorator.json_library = ujson
    MockContentBase.f_content_plain = base64.b64encode(b"{" + Setup.original)
    lines = benchmark.pedantic(wrap, iterations=1, rounds=100)
    original: bytes = base64.b64decode(MockContentBase.f_content_plain)
    original_length: int = len(original)

    assert lines[-1][1] == original_length
