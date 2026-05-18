from __future__ import annotations

import pytest

from altera_api.ingestion.csv_reader import (
    CSVReadConfig,
    CSVReadError,
    read_table_bytes,
)


def test_reads_comma_csv() -> None:
    data = b"product_name,weight_per_item_kg\nLentil Soup,0.4\nMilk,1.0\n"
    table = read_table_bytes(data)
    assert table.headers == ("product_name", "weight_per_item_kg")
    assert table.rows == (
        {"product_name": "Lentil Soup", "weight_per_item_kg": "0.4"},
        {"product_name": "Milk", "weight_per_item_kg": "1.0"},
    )


def test_reads_tsv() -> None:
    data = b"product_name\tweight_per_item_kg\nLentil Soup\t0.4\n"
    table = read_table_bytes(data, config=CSVReadConfig(delimiter="\t"))
    assert table.rows[0]["product_name"] == "Lentil Soup"


def test_normalises_headers_at_read() -> None:
    data = b"Product Name,Weight-per-item-kg\nx,0.4\n"
    table = read_table_bytes(data)
    assert table.headers == ("product_name", "weight_per_item_kg")
    assert table.rows[0] == {"product_name": "x", "weight_per_item_kg": "0.4"}


def test_strips_utf8_bom() -> None:
    data = "﻿product_name,weight_per_item_kg\nx,0.4\n".encode()
    table = read_table_bytes(data)
    assert table.headers == ("product_name", "weight_per_item_kg")


def test_handles_quoted_commas() -> None:
    data = b'product_name,ingredients_text\n"Lentil Soup","red lentils, water, salt"\n'
    table = read_table_bytes(data)
    assert table.rows[0]["ingredients_text"] == "red lentils, water, salt"


def test_detects_duplicate_headers() -> None:
    data = b"product_name,brand,brand\nx,A,B\n"
    table = read_table_bytes(data)
    assert "brand" in table.duplicate_headers


def test_rejects_empty_file() -> None:
    with pytest.raises(CSVReadError):
        read_table_bytes(b"")


def test_rejects_oversize() -> None:
    big = b"product_name\n" + (b"x\n" * 10)
    with pytest.raises(CSVReadError):
        read_table_bytes(big, config=CSVReadConfig(max_bytes=10))


def test_rejects_too_many_rows() -> None:
    data = b"product_name\n" + (b"x\n" * 5)
    with pytest.raises(CSVReadError):
        read_table_bytes(data, config=CSVReadConfig(max_rows=2))


def test_rejects_non_utf8() -> None:
    with pytest.raises(CSVReadError):
        read_table_bytes(b"\xff\xfeproduct_name\nfoo\n")


def test_pads_short_rows() -> None:
    data = b"a,b,c\n1,2\n"
    table = read_table_bytes(data)
    assert table.rows[0] == {"a": "1", "b": "2", "c": ""}
