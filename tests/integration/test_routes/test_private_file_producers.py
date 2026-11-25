"""Actual file writers through private reads and verified local materialization.

Only external provider/TDLib transport and Supabase HTTP are substituted here.
The package bridge, file projection, managed copies, ArtifactRepository and
authenticated REST download path are real. Real Auth/PG/HTTP proof is separate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlmodel import Session, select
from stackos_connectors import ConnectorClient, ConnectorFile, ConnectorResult
from stackos_connectors.catalog import load_registry

from stackos.actions.aignc import AigncActionConnector, _audio_artifact
from stackos.actions.connectors import ActionConnectorError, ActionConnectorRequest
from stackos.actions.elevenlabs import ElevenLabsActionConnector
from stackos.actions.google_veo import GoogleVeoVideoActionConnector
from stackos.actions.hubspot import _download_bulk_export_artifact
from stackos.actions.openai_images import OpenAIImagesActionConnector
from stackos.actions.telegram import TelegramActionConnector, _AccountClient
from stackos.auth import UserPrincipal
from stackos.auth_providers.repository.schema import ResolvedCredential
from stackos.db.models import Artifact, Credential, IntegrationCredential, Project
from stackos.mcp.bridge.downloads import materialize_download
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ArtifactRepository
from tests.integration.test_repositories.test_telegram_actions import FakeTelegram
from tests.unit.test_server_auth import online as online_fixture
from tests.unit.test_server_auth import sign_in

online = online_fixture
_ASYNC_HTTP_CLIENT = httpx.AsyncClient


@pytest.mark.parametrize(
    "producer", ["openai", "hubspot", "telegram", "elevenlabs-speech", "elevenlabs-design"]
)
@pytest.mark.parametrize("principal_context", ["request", "background"])
def test_actual_producer_to_private_local_download(
    online, monkeypatch, tmp_path, producer, principal_context
):
    client, settings, state = online
    expected = b"synthetic file bytes\n"
    with Session(client.app.state.engine) as session:
        project = Project(
            slug="produced-file-" + uuid4().hex,
            name="Produced files",
            domain="producer.test",
            locale="en",
            owner_user_id=state["user_id"],
            environment_id=settings.environment_id,
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        session.info["user_principal"] = UserPrincipal(state["user_id"], settings.environment_id)
        if principal_context == "background":
            # Internal producers retain the persisted online Project even when
            # their session does not carry an HTTP request's user principal.
            session.info.pop("user_principal")
        request = ActionConnectorRequest(
            project_id=project.id,
            plugin_slug="utils",
            action_key="fixture",
            action_ref="utils.fixture",
            provider_key=producer,
            operation="fixture",
            input_json={},
            config_json={},
            asset_dir=settings.generated_assets_dir,
            session=session,
        )
        if producer == "openai":
            output = _produce_image(request, monkeypatch, expected)
            artifact_id = output["data"][0]["artifact_id"]
            uri = output["data"][0]["url"]
        elif producer == "hubspot":
            output = _produce_export(request, monkeypatch, expected)
            artifact_id, uri = output["artifact_id"], output["artifact_ref"]
        elif producer == "telegram":
            output = _produce_telegram_file(request, tmp_path, expected)
            artifact_id, uri = output["artifact_id"], output["artifact_ref"]
        else:
            output = _produce_audio(request, monkeypatch, expected, producer)
            artifact_id = (
                output["artifact_id"]
                if producer == "elevenlabs-speech"
                else output["previews"][0]["artifact_id"]
            )
            uri = ArtifactRepository(session).get(artifact_id).uri
        project_id = project.id

    login = sign_in(client)
    client.headers["X-CSRF-Token"] = login["csrf_token"]
    arguments = {"project_id": project_id, "artifact_id": artifact_id}
    response = client.post(
        "/api/v1/operations/artifact.download/call", json={"arguments": arguments}
    )
    # Assert this first so the original writer/reader mismatch is the red signal.
    assert response.status_code == 200, response.text
    assert uri.startswith(f"/generated-assets/projects/{project_id}/")
    descriptor = response.json()
    response = client.post(
        "/api/v1/operations/artifact.read/call",
        json={"arguments": {**arguments, "response_mode": "raw"}},
    )
    assert response.status_code == 200 and "error" not in response.json(), response.text
    if producer in {"hubspot", "telegram"}:
        assert response.json()["content"] == expected.decode()
    destination = tmp_path / f"downloaded-{producer}.bin"
    result = materialize_download(
        client,
        server_url=settings.server_url,
        headers={},
        tool_name="artifact.download",
        arguments=arguments,
        descriptor=descriptor,
        destination_path=str(destination),
    )
    assert result["local_path"] == str(destination)
    assert destination.read_bytes() == expected
    assert result["sha256"] == hashlib.sha256(expected).hexdigest()
    assert str(settings.generated_assets_dir) not in json.dumps(result)
    state["user_id"] = str(uuid4())
    sign_in(client)
    assert client.get(descriptor["download_uri"]).status_code == 404


def _produce_image(request, monkeypatch, expected):
    async def provider(_client, key, action_key, native_data, auth, options):
        path = options.output_dir / "synthetic-image.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(expected)
        return ConnectorResult(
            output_json={"data": [{"path": str(path), "file_format": "png"}]},
            files=[ConnectorFile(path=str(path), mime_type="image/png")],
        )

    monkeypatch.setattr(ConnectorClient, "execute", provider)
    credential = ResolvedCredential(
        credential=Credential(provider_key="openai-images", auth_method_key="api_key"),
        integration=IntegrationCredential(encrypted_payload=b"unused", nonce=b"0" * 12),
        secret_payload=b"synthetic-provider-key",
        config_json={"auth_method_key": "api_key"},
    )
    definition = next(
        item
        for item in load_registry("connectors/openai_images/catalog.json").actions.values()
        if item.operation == "image.generate"
    )
    request = replace(
        request,
        credential=credential,
        action_key=definition.key,
        action_ref="utils." + definition.key,
        provider_key="openai-images",
        operation="image.generate",
        input_json={"prompt": "synthetic"},
    )
    return asyncio.run(OpenAIImagesActionConnector().execute(request)).output_json


def _produce_audio(request, monkeypatch, expected, producer):
    async def provider(native):
        path = native.asset_dir / "synthetic-audio.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(expected)
        return ConnectorResult(
            output_json=(
                {"output_format": "mp3_44100_128"}
                if producer == "elevenlabs-speech"
                else {"previews": [{"generated_voice_id": "synthetic", "file_index": 0}]}
            ),
            files=[ConnectorFile(path=str(path), mime_type="audio/mpeg")],
        )

    connector = ElevenLabsActionConnector()
    monkeypatch.setattr(connector, "execute_native", provider)
    request = replace(
        request,
        operation="speech.generate" if producer == "elevenlabs-speech" else "voices.design",
    )
    return asyncio.run(connector.execute(request)).output_json


@pytest.mark.parametrize("consumer", ["elevenlabs", "aignc", "openai-images", "google-veo"])
@pytest.mark.parametrize("principal_context", ["request", "background"])
def test_media_consumers_reject_foreign_files_and_aliases(
    online, monkeypatch, consumer, principal_context
):
    from stackos_connectors.connectors.aignc.contract import AIGNC_AUDIO_MODELS

    client, settings, state = online
    calls = []
    with Session(client.app.state.engine) as session:
        projects = []
        for owner in (state["user_id"], str(uuid4())):
            project = Project(
                slug="audio-input-" + uuid4().hex,
                name="Audio input",
                domain="producer.test",
                locale="en",
                owner_user_id=owner,
                environment_id=settings.environment_id,
            )
            session.add(project)
            session.commit()
            session.refresh(project)
            path = settings.generated_assets_dir / "projects" / str(project.id) / "voice.mp3"
            path.parent.mkdir(parents=True)
            path.write_bytes(str(project.id).encode())
            projects.append((project.id, path))
        own, foreign = projects
        if principal_context == "request":
            session.info["user_principal"] = UserPrincipal(
                state["user_id"], settings.environment_id
            )
        artifact = (
            ArtifactRepository(session)
            .create(
                project_id=own[0],
                kind="audio",
                mime_type="audio/mpeg",
                uri="/generated-assets/"
                + own[1].relative_to(settings.generated_assets_dir).as_posix(),
            )
            .data
        )
        request = ActionConnectorRequest(
            project_id=own[0],
            plugin_slug="utils",
            action_key="fixture",
            action_ref="utils.fixture",
            provider_key=consumer,
            operation={
                "elevenlabs": "alignment.create",
                "aignc": "audio.analyze",
                "openai-images": "image.edit",
                "google-veo": "video.generate",
            }[consumer],
            input_json={
                "audio_artifact_id": artifact.id,
                "text": "synthetic",
                "instruction": "synthetic",
                "model": next(iter(AIGNC_AUDIO_MODELS)),
                "output_limit": 10,
                "prompt": "synthetic",
                "input_image_refs": [artifact.uri],
                "input_image_ref": artifact.uri,
            },
            config_json={},
            asset_dir=settings.generated_assets_dir,
            session=session,
            credential=ResolvedCredential(
                credential=Credential(provider_key=consumer, auth_method_key="api_key"),
                integration=IntegrationCredential(encrypted_payload=b"unused", nonce=b"0" * 12),
                secret_payload=b"synthetic-provider-key",
                config_json={},
            ),
        )
        if consumer == "elevenlabs":
            connector = ElevenLabsActionConnector()

            async def provider(native):
                calls.append(Path(native.input_json["audio_path"]).read_bytes())
                return ConnectorResult(output_json={"words": []})

            monkeypatch.setattr(connector, "execute_native", provider)
            asyncio.run(connector.execute(request))
            assert calls == [str(own[0]).encode()]
        elif consumer == "aignc":
            connector = AigncActionConnector()
            assert _audio_artifact(request)[0] == own[1]
            assert connector.validate(request) == []

            async def audio_provider(_native, payload, **_kwargs):
                calls.append(Path(payload["audio_path"]).read_bytes())
                return SimpleNamespace(data={})

            monkeypatch.setattr("stackos.actions.aignc.execute_media_native", audio_provider)
            asyncio.run(connector.execute(request))
            assert calls == [str(own[0]).encode()]
        else:
            connector = (
                OpenAIImagesActionConnector()
                if consumer == "openai-images"
                else GoogleVeoVideoActionConnector()
            )

            async def image_provider(_native, payload, **_kwargs):
                path = (
                    payload["input_image_paths"][0]
                    if consumer == "openai-images"
                    else payload["input_image_path"]
                )
                calls.append(Path(path).read_bytes())
                return SimpleNamespace(data={}, cost_usd=0, metadata={})

            module = "openai_images" if consumer == "openai-images" else "google_veo"
            monkeypatch.setattr(f"stackos.actions.{module}.execute_media_native", image_provider)
            asyncio.run(connector.execute(request))
            assert calls == [str(own[0]).encode()]
        alias = own[1].with_name("alias.mp3")
        alias.symlink_to(foreign[1])
        for forbidden in (foreign[1], alias):
            uri = (
                "/generated-assets/"
                + forbidden.relative_to(settings.generated_assets_dir).as_posix()
            )
            ArtifactRepository(session).update(
                artifact.id, project_id=own[0], fields={"uri"}, uri=uri
            )
            request = replace(
                request,
                input_json={
                    **request.input_json,
                    "input_image_refs": [uri],
                    "input_image_ref": uri,
                },
            )
            # Editable metadata or a namespaced alias cannot adopt another project's bytes.
            before = list(calls)
            with pytest.raises((ActionConnectorError, ValidationError)):
                asyncio.run(connector.execute(request))
            assert calls == before


def test_audio_output_rejects_descriptor_outside_selected_directory(online, monkeypatch):
    client, settings, state = online
    with Session(client.app.state.engine) as session:
        project = Project(
            slug="audio-descriptor-" + uuid4().hex,
            name="Audio descriptor",
            domain="producer.test",
            locale="en",
            owner_user_id=state["user_id"],
            environment_id=settings.environment_id,
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        # Even another file in this project's namespace is not this call's output.
        existing = settings.generated_assets_dir / "projects" / str(project.id) / "existing.mp3"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"retained source")

        async def provider(_request):
            return ConnectorResult(
                output_json={"output_format": "mp3_44100_128"},
                files=[ConnectorFile(path=str(existing), mime_type="audio/mpeg")],
            )

        connector = ElevenLabsActionConnector()
        monkeypatch.setattr(connector, "execute_native", provider)
        request = ActionConnectorRequest(
            project_id=project.id,
            plugin_slug="utils",
            action_key="fixture",
            action_ref="utils.fixture",
            provider_key="elevenlabs",
            operation="speech.generate",
            input_json={},
            config_json={},
            asset_dir=settings.generated_assets_dir,
            session=session,
        )
        with pytest.raises(ActionConnectorError, match="registration failed"):
            asyncio.run(connector.execute(request))
        assert session.exec(select(Artifact).where(Artifact.project_id == project.id)).all() == []
        assert existing.read_bytes() == b"retained source"


def _produce_export(request, monkeypatch, expected):
    def response(call):
        assert call.method == "GET" and call.url.host == "exports.hubspot.example"
        return httpx.Response(
            200,
            content=expected,
            headers={
                "content-type": "text/csv",
                "content-disposition": 'attachment; filename="result.csv"',
            },
        )

    # Restore the Auth substitute before making authenticated REST calls.
    with monkeypatch.context() as transport:
        transport.setattr(
            "stackos.actions.hubspot.httpx.AsyncClient",
            lambda **kw: _ASYNC_HTTP_CLIENT(**kw, transport=httpx.MockTransport(response)),
        )
        return asyncio.run(
            _download_bulk_export_artifact(
                request,
                job=SimpleNamespace(safe_ref="synthetic-export-job"),
                job_metadata={
                    "export_name": "Synthetic export",
                    "format": "CSV",
                    "object_type": "contacts",
                    "property_refs": [],
                    "associated_object_types": [],
                },
                result_url="https://exports.hubspot.example/result?signature=synthetic",
            )
        )


def _produce_telegram_file(request, tmp_path, expected):
    files = tmp_path / "private-tdlib-files"
    files.mkdir()
    (files / "test.txt").write_bytes(expected)
    runtime = FakeTelegram(files)
    request = replace(
        request,
        credential=ResolvedCredential(
            credential=Credential(provider_key="telegram", auth_method_key="tdlib-bot-token"),
            integration=IntegrationCredential(encrypted_payload=b"unused", nonce=b"0" * 12),
            secret_payload=b"synthetic-tdlib",
            config_json={},
        ),
    )
    native = _AccountClient(runtime, "cred_synthetic", generation=1, request=request)
    return asyncio.run(TelegramActionConnector(runtime)._download(native, request, 42))
