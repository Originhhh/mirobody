"""The deployment presets agree on weights, context and the canonical template."""

import configparser
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _preset(name):
    preset = configparser.ConfigParser(interpolation=None)
    # llama.cpp puts its format version before the first model section.
    source = (ROOT / "docker" / name).read_text(encoding="utf-8")
    preset.read_string("[header]\n" + source)
    assert preset["header"]["version"] == "1"
    return preset


@pytest.mark.parametrize("name", ["local-models.ini", "local-models-cpu.ini"])
def test_small_weights_and_template_agree(name):
    small = _preset(name)["minicpm5-2b"]
    assert small["hf-repo"] == "openbmb/MiniCPM5-2B-GGUF:Q8_0"
    assert small["chat-template-file"] == "docker/minicpm5-2b.jinja"
    assert (ROOT / small["chat-template-file"]).is_file()


def test_cpu_context_retains_unquantized_kv_cache():
    preset = _preset("local-models-cpu.ini")
    small = preset["minicpm5-2b"]
    effective = dict(preset["*"]) | dict(small)
    assert small["c"] == "32768"
    assert effective.get("cache-type-k", "f16") == "f16"
    assert effective.get("cache-type-v", "f16") == "f16"


def test_gpu_context_uses_q8_kv_cache():
    small = _preset("local-models.ini")["minicpm5-2b"]
    assert small["c"] == "65536"
    assert small["cache-type-k"] == "q8_0"
    assert small["cache-type-v"] == "q8_0"
