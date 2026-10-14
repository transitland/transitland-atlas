"""Tests for URL handling in collect-odpt-gtfs.py.

Run: uv run --with pytest pytest external-data-for-reference/ -q
"""

import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
_spec = importlib.util.spec_from_file_location(
    "collect_odpt_gtfs", os.path.join(_HERE, "collect-odpt-gtfs.py"))
odpt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(odpt)

RT = "https://api.odpt.org/api/v4/gtfs/realtime/odpt_X_AllLines_vehicle"


def rt_urls(url, token=None):
    collector = odpt.ODPTGTFSCollector(token)
    info = {"vehicle_position": {"is_operated": True, "url": url}}
    return collector.get_gtfsrt_urls(info, include_access_token=bool(token))


def test_placeholder_key_parameter_is_dropped():
    assert rt_urls(f"{RT}?acl:consumerKey={odpt.ODPT_KEY_PLACEHOLDER}") == {
        "realtime_vehicle_positions": RT}


def test_other_parameters_are_kept():
    got = rt_urls(f"{RT}?acl:ConsumerKey={odpt.ODPT_KEY_PLACEHOLDER}&date=current")
    assert got == {"realtime_vehicle_positions": f"{RT}?date=current"}


def test_token_fills_the_placeholder():
    got = rt_urls(f"{RT}?acl:consumerKey={odpt.ODPT_KEY_PLACEHOLDER}", token="k")
    assert got == {"realtime_vehicle_positions": f"{RT}?acl:consumerKey=k"}


def test_host_follows_the_currently_valid_file():
    collector = odpt.ODPTGTFSCollector()
    info = {"organization_id": "O", "dataset_id": "D", "data_resources": [
        {"url": "https://api-public.odpt.org/api/v4/files/odpt/O/D.zip?date=2020", "is_feed_available_period": False},
        {"url": "https://api.odpt.org/api/v4/files/odpt/O/D.zip?date=2026", "is_feed_available_period": True},
    ]}
    assert collector.get_gtfs_download_url(info).startswith("https://api.odpt.org/")
    info["data_resources"] = None
    assert collector.get_gtfs_download_url(info).startswith("https://api.odpt.org/")
