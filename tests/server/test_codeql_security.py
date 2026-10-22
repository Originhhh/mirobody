"""Synthetic checks for the shared boundaries flagged by CodeQL."""

import hashlib
import importlib
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mirobody.utils import http
from mirobody.utils.config.config import Config
from mirobody.utils.config.storage.local import LocalStorage
from mirobody.utils.log import secret_fingerprint


@pytest.mark.parametrize("status,code", [(200, 0), (400, -1), (500, -3)])
@pytest.mark.parametrize("helper", ["json_response", "json_response_with_code"])
def test_response_body_is_preserved_but_never_logged(caplog, status, code, helper):
    message = "synthetic-private-message@example.invalid"
    with caplog.at_level(logging.INFO, logger=http.__name__):
        if helper == "json_response":
            response = http.json_response({"message": message}, status_code=status)
        else:
            response = http.json_response_with_code(code=code, msg=message, status=status)
    assert message in json.loads(response.body).values()
    assert message not in caplog.text
    assert caplog.records[-1].status == status


@pytest.mark.parametrize("value", ["a", "short", "synthetic-secret-prefix-and-suffix"])
def test_config_mask_does_not_reveal_credential_fragments(value):
    assert Config.to_masked_str(value) == "[configured]"
    assert Config.to_masked_str("") == ""


def test_log_fingerprint_is_keyed_and_correlates_within_a_process(monkeypatch):
    value = "synthetic@example.invalid"
    assert secret_fingerprint(value) == secret_fingerprint(value)
    assert secret_fingerprint(value) != secret_fingerprint(value + "x")
    assert secret_fingerprint(value) != "sha256:" + hashlib.sha256(value.encode()).hexdigest()[:12]
    assert value not in secret_fingerprint(value)
    assert secret_fingerprint(None) == secret_fingerprint("") == "<none>"
    from mirobody.utils import log

    previous = secret_fingerprint(value)
    monkeypatch.setattr(log, "_fingerprint_key", b"synthetic-different-key")
    assert secret_fingerprint(value) != previous


@pytest.mark.parametrize("host", ["smtp.mandrillapp.com", "SMTP.MANDRILLAPP.COM", "smtp.mandrillapp.com."])
def test_exact_mandrill_host_uses_mandrill_api(monkeypatch, host):
    from mirobody.user.auth import email

    monkeypatch.setattr(email, "MandrillEmailValidator", lambda **kwargs: "mandrill")
    monkeypatch.setattr(email, "SMTPEmailValidator", lambda **kwargs: "smtp")
    assert email.create_email_validator(
        smtp_host=host, smtp_user="synthetic", smtp_pass="synthetic", from_email="from@example.invalid", template="test",
    ) == "mandrill"


@pytest.mark.parametrize("host", ["smtp.mandrillapp.com.evil.invalid", "evil-mandrillapp.com", "mandrillapp.com.evil.invalid"])
def test_similar_smtp_host_uses_configured_smtp_transport(monkeypatch, host):
    from mirobody.user.auth import email

    monkeypatch.setattr(email, "MandrillEmailValidator", lambda **kwargs: "mandrill")
    monkeypatch.setattr(email, "SMTPEmailValidator", lambda **kwargs: "smtp")
    assert email.create_email_validator(
        smtp_host=host, smtp_user="synthetic", smtp_pass="synthetic", from_email="from@example.invalid", template="test",
    ) == "smtp"


def test_synthetic_provider_addresses_do_not_collide_on_old_md5_prefix():
    from mirobody.user.platform import PlatformUserService

    service = PlatformUserService()
    # These invented ids share the previous eight-character MD5 prefix.
    first = service._generate_unique_email("theta_garmin", "102")
    second = service._generate_unique_email("theta_garmin", "44553")
    assert first != second
    assert first == service._generate_unique_email("theta_garmin", "102")


@pytest.mark.parametrize("key", ["../outside/private.txt", "../uploads-sibling/private.txt", "nested/../../outside/private.txt"])
@pytest.mark.asyncio
async def test_local_storage_refuses_escape_for_every_operation(tmp_path, key):
    root = tmp_path / "uploads"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_bytes(b"synthetic-private-data")
    storage = LocalStorage(base_path=str(root), proxy_url="/files")
    assert (await storage.put(key, b"replace"))[1]
    assert (await storage.get(key))[1]
    assert await storage.delete(key)
    assert (await storage.generate_signed_url(key))[1]
    assert (outside / "private.txt").read_bytes() == b"synthetic-private-data"


@pytest.mark.asyncio
async def test_local_storage_rejects_symlink_escape_and_allows_nested_files(tmp_path):
    root = tmp_path / "uploads"
    outside = tmp_path / "outside"
    outside.mkdir()
    storage = LocalStorage(base_path=str(root), prefix="tenant", proxy_url="/files")
    (root / "tenant").mkdir()
    (root / "tenant" / "escape").symlink_to(outside, target_is_directory=True)
    assert (await storage.put("escape/private.txt", b"blocked"))[1]
    assert not (outside / "private.txt").exists()
    url, error = await storage.put("nested/allowed.txt", b"synthetic")
    assert url == "/files/tenant/nested/allowed.txt" and error is None
    assert await storage.get("nested/allowed.txt") == (b"synthetic", None)
    assert await storage.delete("nested/allowed.txt") is None


def test_ephemeral_lookup_index_remains_byte_identical():
    from mirobody.utils.ephemeral import EphemeralStore

    value = "synthetic-oauth-state"
    assert EphemeralStore._hash(value) == hashlib.sha256(value.encode()).digest()


def test_storage_failure_log_never_includes_vendor_exception_text(caplog):
    from mirobody.utils.config.storage.abstract import storage_failure

    message = "synthetic-secret-from-vendor"
    try:
        raise ValueError(message)
    except ValueError as exc:
        with caplog.at_level(logging.ERROR):
            answer = storage_failure("download", "synthetic-key", exc)
    assert message not in caplog.text
    assert answer == "download failed (ValueError)"
    assert "synthetic-key" not in caplog.text


def test_standardize_error_cannot_echo_an_arbitrary_extraction_exception(monkeypatch):
    from mirobody import engine
    from mirobody.engine.parse import ExtractionError
    records_router = importlib.import_module("mirobody.server.routers.records_router")

    async def fail(*args, **kwargs):
        raise ExtractionError("synthetic-private-report-content")

    monkeypatch.setattr(engine, "parse_text", fail)
    app = FastAPI()
    app.include_router(records_router.router)
    app.dependency_overrides[records_router.verify_token] = lambda: "1"
    response = TestClient(app).post("/api/standardize", json={"text": "synthetic"})
    assert response.status_code == 400
    assert "synthetic-private-report-content" not in response.text
    assert response.json()["error"]["code"] == "extraction_failed"


def test_failed_provider_decryption_logs_only_ciphertext_fingerprint(monkeypatch, caplog):
    service_module = importlib.import_module("mirobody.collect.providers._platform.database_service")
    monkeypatch.setattr(service_module, "decrypt_string_aes_gcm", lambda value: None)
    with caplog.at_level(logging.ERROR):
        assert service_module.ProviderDatabaseService()._decrypt("synthetic-ciphertext", "7") is None
    assert "synthetic-ciphertext" not in caplog.text
    assert secret_fingerprint("synthetic-ciphertext") in caplog.text


def test_unavailable_model_diagnostic_contains_metadata_not_key_value(monkeypatch, caplog):
    from mirobody.utils.config import llm
    from mirobody.utils.llm import utils

    spec = llm.RouteSpec(alias="synthetic", model="synthetic", api_key_env="SYNTHETIC_API_KEY", base_url="")
    monkeypatch.setattr(llm, "route_candidates", lambda surface: [spec])
    monkeypatch.setattr(llm, "read_api_key", lambda name: "synthetic-private-value")
    monkeypatch.setattr(llm, "_fits", lambda surface, candidate: True)
    monkeypatch.setattr(utils, "resolve_route", lambda surface: None)
    with caplog.at_level(logging.ERROR):
        assert utils._route(None, None, "text") is None
    assert "SYNTHETIC_API_KEY" in caplog.text
    assert "synthetic-private-value" not in caplog.text


@pytest.mark.asyncio
async def test_existing_provider_link_never_regenerates_internal_email(monkeypatch):
    module = importlib.import_module("mirobody.user.platform")

    async def existing(**kwargs):
        return [{"user_id": "7", "is_del": False, "reconnect": 0}]

    monkeypatch.setattr(module, "execute_query", existing)
    service = module.PlatformUserService()
    monkeypatch.setattr(service, "_generate_unique_email", lambda *args: pytest.fail("existing link must retain its account"))
    assert await service.find_or_create_user_by_provider_id("theta_garmin", "102", "UTC") == "7"
