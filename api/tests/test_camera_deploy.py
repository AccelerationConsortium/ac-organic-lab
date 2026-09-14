"""The cutover may edit camera routes only, preserving unrelated live policy."""

import copy
import importlib.util
from pathlib import Path


def test_route_cutover_preserves_non_camera_configuration():
    path = Path(__file__).resolve().parents[2] / "tools/deploy-camera-viewing.py"
    spec = importlib.util.spec_from_file_location("camera_deploy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    other = {
        "match": [{"path": ["/private/*"]}],
        "handle": [
            {
                "handler": "subroute",
                "routes": [{"handle": [{"handler": "static_response", "status_code": 403}]}],
            }
        ],
    }
    raw = {
        "match": [{"path": ["/streams/*"]}],
        "group": "keep",
        "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "127.0.0.1:1984"}]}],
    }
    state = {
        "admin": {"listen": "127.0.0.1:2019"},
        "routes": [copy.deepcopy(other), copy.deepcopy(raw)],
    }
    assert module.configure(state) == 1
    assert state["routes"][0] == other
    assert state["admin"] == {"listen": "127.0.0.1:2019"}
    assert state["routes"][1]["match"] == [{"path": ["/api/camera-streams/*"]}]
    assert state["routes"][2]["group"] == "keep"
    assert state["routes"][2]["handle"][0]["status_code"] == 410
