import pytest

from stackos.artifacts.redaction import redact_secret_text, redact_secrets
from stackos.secret_refs import redact_secret_values


def test_exact_public_revision_field_retains_source_text():
    source = '{"SyncToken":"2", "TotalAmt":9007199254740993.123456789, "Balance":0.00}'
    assert redact_secret_text(source) == source
    assert redact_secrets({"raw_json": source}) == {"raw_json": source}


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "access_token",
        "client_secret",
        "api_key",
        "authorization",
        "provider_access_token",
        "bearerToken",
        "my_api_key",
        "private_key",
        "privateKey",
        "serviceAccountJson",
        "providerPrivateKey",
        "providerServiceAccountJson",
        "refresh_token",
        "service_account_json",
        "prefixSyncToken",
    ],
)
def test_complete_assignment_keys_still_redact_credentials(key):
    source = f'{{"{key}":"unknown-provider-secret"}}'
    assert redact_secret_text(source) == f'{{"{key}":"[redacted]"}}'
    assert redact_secret_text(f"{key}=unknown-provider-secret") == f"{key}=[redacted]"


def test_public_revision_exception_never_bypasses_known_secret_values():
    source = {"raw_json": '{"SyncToken":"actual-credential-value"}'}
    safe = redact_secrets(redact_secret_values(source, ["actual-credential-value"]))
    assert "actual-credential-value" not in str(safe)
    # Structured unknown token fields retain their existing conservative posture.
    assert redact_secrets({"SyncToken": "unknown-value"}) == {"SyncToken": "[redacted]"}


def test_verified_reference_preservation_is_exact_and_never_preserves_keys_or_secrets():
    ref = "provider-object:registered-US-reference"
    source = {
        ref: "US",
        "nested": [ref, {"lookalike": "provider-object:unregistered-US-reference"}],
        "tuple": (ref,),
        "text": f"ref {ref}",
    }
    safe = redact_secret_values(source, ("US",), preserve_value=lambda value: value == ref)
    assert safe == {
        "provider-object:registered-[redacted]-reference": "[redacted]",
        "nested": [ref, {"lookalike": "provider-object:unregistered-[redacted]-reference"}],
        "tuple": (ref,),
        "text": "ref provider-object:registered-[redacted]-reference",
    }
    assert redact_secret_values(ref, (ref,), preserve_value=lambda _: True) == "[redacted]"


def test_reference_verification_is_not_called_for_values_without_a_secret_collision():
    def unexpected_verification(_value: str) -> bool:
        raise AssertionError("an unchanged output does not need an ownership lookup")

    assert redact_secret_values(
        {"value": "provider-object:unchanged"}, ("US",), preserve_value=unexpected_verification
    ) == {"value": "provider-object:unchanged"}


def test_text_bearer_and_private_key_redaction_is_unchanged():
    assert "credential-value" not in redact_secret_text("authorization: Bearer credential-value")
    assert "credential-value" not in redact_secret_text("Bearer credential-value")
    assert "private-material" not in redact_secret_text(
        "-----BEGIN PRIVATE KEY-----private-material-----END PRIVATE KEY-----"
    )


def test_redact_secrets_redacts_signed_url_values_in_nested_strings() -> None:
    payload = {
        "media": {
            "first_frame_url": (
                "https://cdn.example.com/input.png?X-Amz-Credential=abc&"
                "X-Amz-Signature=def&Expires=9999999999&safe=value"
            )
        }
    }

    redacted = redact_secrets(payload)

    assert redacted["media"]["first_frame_url"] == (
        "https://cdn.example.com/input.png?X-Amz-Credential=[redacted]&"
        "X-Amz-Signature=[redacted]&Expires=[redacted]&safe=value"
    )


def test_redact_secret_text_redacts_short_signature_query_params() -> None:
    assert redact_secret_text("https://example.test/file.mp4?sig=abc123") == (
        "https://example.test/file.mp4?sig=[redacted]"
    )
