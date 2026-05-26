"""Operation specifications for communication platform operations."""

from __future__ import annotations

from stackos.mcp.contract import WriteEnvelope
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import Page

from .context import communication_context_query
from .ingress import (
    ingress_endpoint_configure,
    ingress_endpoint_refresh,
    ingress_endpoint_routes,
    ingress_endpoint_status,
    ingress_endpoint_sync,
)
from .profiles import (
    communication_profile_get,
    communication_profile_list,
    communication_profile_upsert,
)
from .records import (
    communication_contact_list,
    communication_contact_upsert,
    communication_membership_list,
    communication_membership_upsert,
    communication_route_list,
    communication_route_upsert,
    communication_surface_list,
    communication_surface_upsert,
    communication_target_list,
    communication_target_resolve,
    communication_target_upsert,
)
from .schemas import (
    CommunicationContactListInput,
    CommunicationContactOut,
    CommunicationContactUpsertInput,
    CommunicationContextQueryInput,
    CommunicationContextQueryOut,
    CommunicationMembershipListInput,
    CommunicationMembershipOut,
    CommunicationMembershipUpsertInput,
    CommunicationProfileGetInput,
    CommunicationProfileListInput,
    CommunicationProfileOut,
    CommunicationProfileUpsertInput,
    CommunicationRouteListInput,
    CommunicationRouteOut,
    CommunicationRouteUpsertInput,
    CommunicationSurfaceListInput,
    CommunicationSurfaceOut,
    CommunicationSurfaceUpsertInput,
    CommunicationTargetListInput,
    CommunicationTargetOut,
    CommunicationTargetResolveInput,
    CommunicationTargetResolveOut,
    CommunicationTargetUpsertInput,
    IngressEndpointConfigureInput,
    IngressEndpointOut,
    IngressEndpointRefreshInput,
    IngressEndpointRoutesInput,
    IngressEndpointRoutesOut,
    IngressEndpointStatusInput,
    IngressEndpointStatusOut,
    IngressEndpointSyncInput,
    IngressEndpointSyncOut,
)


def _surfaces(name: str, command: str) -> OperationSurfaces:
    return OperationSurfaces(
        mcp=OperationSurface(enabled=True),
        rest=OperationSurface(enabled=True, path=f"/api/v1/operations/{name}/call"),
        cli=OperationSurface(enabled=True, command=command),
    )


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="ingressEndpoint.configure",
            summary="Configure one project-level public ingress endpoint.",
            input_model=IngressEndpointConfigureInput,
            output_model=WriteEnvelope[IngressEndpointOut],
            handler=ingress_endpoint_configure,
            surfaces=_surfaces(
                "ingressEndpoint.configure",
                "ops call ingressEndpoint.configure",
            ),
            purpose=(
                "Use this setup operation to define the project public ingress base URL. "
                "The endpoint is provider-neutral; driver-specific details live only in "
                "driver_config and routes are derived from project communication profiles."
            ),
            prerequisites=(
                "Use driver=public-url for a deployed/reverse-proxied HTTPS host.",
                "Use driver=local-tunnel only for local development and keep provider "
                "settings in driver_config.",
                "Never store provider secrets in this resource.",
            ),
            returns=("A WriteEnvelope with the safe IngressEndpointOut record.",),
            examples=(
                OperationExample(
                    title="Configure deployed HTTPS ingress",
                    arguments={
                        "project_id": 1,
                        "driver": "public-url",
                        "public_base_url": "https://stackos.example.com",
                    },
                ),
                OperationExample(
                    title="Configure local tunnel discovery",
                    arguments={
                        "project_id": 1,
                        "driver": "local-tunnel",
                        "driver_config": {
                            "provider": "ngrok",
                            "discovery_url": "http://127.0.0.1:4040/api/endpoints",
                        },
                    },
                ),
            ),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="ingressEndpoint.refresh",
            summary="Refresh the endpoint public URL from explicit input or driver discovery.",
            input_model=IngressEndpointRefreshInput,
            output_model=WriteEnvelope[IngressEndpointSyncOut],
            handler=ingress_endpoint_refresh,
            surfaces=_surfaces("ingressEndpoint.refresh", "ops call ingressEndpoint.refresh"),
            purpose=(
                "Use this after a local tunnel or deployment URL changes. It stores one "
                "project public_base_url and can sync derived provider routes."
            ),
            prerequisites=("Configure ingressEndpoint first.",),
            returns=("A WriteEnvelope with endpoint, route, and provider sync status.",),
            examples=(
                OperationExample(
                    title="Refresh from local tunnel provider API",
                    arguments={"project_id": 1, "key": "default"},
                ),
            ),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="ingressEndpoint.routes",
            summary="List provider webhook URLs derived from the current public ingress endpoint.",
            input_model=IngressEndpointRoutesInput,
            output_model=IngressEndpointRoutesOut,
            handler=ingress_endpoint_routes,
            surfaces=_surfaces("ingressEndpoint.routes", "ops call ingressEndpoint.routes"),
            purpose="Use this to get exact Slack and Telegram webhook URLs without guessing.",
            prerequisites=("Configure ingressEndpoint first.",),
            returns=("Endpoint metadata plus provider route URLs.",),
            examples=(OperationExample(title="List routes", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="ingressEndpoint.sync",
            summary="Sync the current ingress endpoint into provider profile route metadata.",
            input_model=IngressEndpointSyncInput,
            output_model=WriteEnvelope[IngressEndpointSyncOut],
            handler=ingress_endpoint_sync,
            surfaces=_surfaces("ingressEndpoint.sync", "ops call ingressEndpoint.sync"),
            purpose=(
                "Use this after configuring profiles or public_base_url. It updates safe route "
                "metadata and can apply Telegram setWebhook through daemon-held credentials."
            ),
            prerequisites=("Configure ingressEndpoint and communication profiles first.",),
            returns=("Updated routes and per-provider sync results.",),
            examples=(OperationExample(title="Sync routes", arguments={"project_id": 1}),),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="ingressEndpoint.status",
            summary="Inspect ingress endpoint readiness and provider route state.",
            input_model=IngressEndpointStatusInput,
            output_model=IngressEndpointStatusOut,
            handler=ingress_endpoint_status,
            surfaces=_surfaces("ingressEndpoint.status", "ops call ingressEndpoint.status"),
            purpose="Use this before telling an operator to ping Slack or Telegram.",
            prerequisites=("Pass project_id.",),
            returns=("Configured/ready booleans, endpoint metadata, routes, and notes.",),
            examples=(OperationExample(title="Ingress status", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationProfile.upsert",
            summary="Create or update a provider-neutral communication profile.",
            input_model=CommunicationProfileUpsertInput,
            output_model=WriteEnvelope[CommunicationProfileOut],
            handler=communication_profile_upsert,
            surfaces=_surfaces(
                "communicationProfile.upsert",
                "ops call communicationProfile.upsert",
            ),
            purpose=(
                "Use this setup operation for the agent-facing identity and policy bundle "
                "that can span Telegram, Slack, local chat, SMTP, IMAP, and future transports. "
                "Provider-specific credentials and sends remain explicit provider actions."
            ),
            prerequisites=(
                "Pass identity.display_name.",
                "Keep policy declarative; agents still decide work and provider calls.",
                "Use provider_facets only for safe provider refs, never tokens or secrets.",
            ),
            returns=("A WriteEnvelope with the safe CommunicationProfileOut record.",),
            examples=(
                OperationExample(
                    title="Create support communication profile",
                    arguments={
                        "project_id": 1,
                        "key": "support",
                        "identity": {"display_name": "Support Agent"},
                    },
                ),
            ),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="communicationProfile.get",
            summary="Get one provider-neutral communication profile.",
            input_model=CommunicationProfileGetInput,
            output_model=CommunicationProfileOut,
            handler=communication_profile_get,
            surfaces=_surfaces("communicationProfile.get", "ops call communicationProfile.get"),
            purpose="Use this to inspect safe profile identity, guidance, facets, and policy.",
            prerequisites=("Pass project_id and key.",),
            returns=("One safe CommunicationProfileOut record.",),
            examples=(
                OperationExample(
                    title="Get profile",
                    arguments={"project_id": 1, "key": "support"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationProfile.list",
            summary="List provider-neutral communication profiles.",
            input_model=CommunicationProfileListInput,
            output_model=Page[CommunicationProfileOut],
            handler=communication_profile_list,
            surfaces=_surfaces("communicationProfile.list", "ops call communicationProfile.list"),
            purpose=(
                "Use this during setup or routing diagnostics to discover communication profiles."
            ),
            prerequisites=("Pass project_id.",),
            returns=("A Page of safe CommunicationProfileOut records.",),
            examples=(OperationExample(title="List profiles", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationSurface.upsert",
            summary="Create or update safe communication surface metadata.",
            input_model=CommunicationSurfaceUpsertInput,
            output_model=WriteEnvelope[CommunicationSurfaceOut],
            handler=communication_surface_upsert,
            surfaces=_surfaces(
                "communicationSurface.upsert",
                "ops call communicationSurface.upsert",
            ),
            purpose=(
                "Use this to register a Telegram chat, Slack channel/DM, email mailbox, "
                "or local chat surface with safe capability metadata."
            ),
            prerequisites=("Pass provider_key, surface_ref, and kind.",),
            returns=("A WriteEnvelope with CommunicationSurfaceOut.",),
            examples=(
                OperationExample(
                    title="Register Slack surface",
                    arguments={
                        "project_id": 1,
                        "surface_ref": "slack-channel:C123",
                        "provider_key": "slack-bot",
                        "kind": "slack-channel",
                    },
                ),
            ),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="communicationSurface.list",
            summary="List safe communication surfaces.",
            input_model=CommunicationSurfaceListInput,
            output_model=Page[CommunicationSurfaceOut],
            handler=communication_surface_list,
            surfaces=_surfaces("communicationSurface.list", "ops call communicationSurface.list"),
            purpose="Use this to inspect known channels, DMs, mailboxes, and local chat surfaces.",
            prerequisites=("Pass project_id. Optional filters are provider_key and kind.",),
            returns=("A Page of CommunicationSurfaceOut records.",),
            examples=(
                OperationExample(
                    title="List Slack surfaces",
                    arguments={"project_id": 1, "provider_key": "slack-bot"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationContact.upsert",
            summary="Create or update a safe communication contact.",
            input_model=CommunicationContactUpsertInput,
            output_model=WriteEnvelope[CommunicationContactOut],
            handler=communication_contact_upsert,
            surfaces=_surfaces(
                "communicationContact.upsert",
                "ops call communicationContact.upsert",
            ),
            purpose=(
                "Use this to map people, customers, teams, bots, or organizations to safe "
                "provider refs without exposing provider tokens or credentials."
            ),
            prerequisites=("Pass key and display_name.",),
            returns=("A WriteEnvelope with CommunicationContactOut.",),
            examples=(
                OperationExample(
                    title="Create customer contact",
                    arguments={
                        "project_id": 1,
                        "key": "customer-acme",
                        "display_name": "Acme Inc.",
                        "kind": "organization",
                    },
                ),
            ),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="communicationContact.list",
            summary="List safe communication contacts.",
            input_model=CommunicationContactListInput,
            output_model=Page[CommunicationContactOut],
            handler=communication_contact_list,
            surfaces=_surfaces("communicationContact.list", "ops call communicationContact.list"),
            purpose="Use this to discover safe cross-provider person/customer/team refs.",
            prerequisites=("Pass project_id.",),
            returns=("A Page of CommunicationContactOut records.",),
            examples=(OperationExample(title="List contacts", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationMembership.upsert",
            summary="Create or update communication membership and permission state.",
            input_model=CommunicationMembershipUpsertInput,
            output_model=WriteEnvelope[CommunicationMembershipOut],
            handler=communication_membership_upsert,
            surfaces=_surfaces(
                "communicationMembership.upsert",
                "ops call communicationMembership.upsert",
            ),
            purpose=(
                "Use this to store whether a profile/contact/bot is joined, invited, removed, "
                "or unknown in a surface, with provider capability and scope diagnostics."
            ),
            prerequisites=("Pass surface_ref, member_ref, provider_key, and status.",),
            returns=("A WriteEnvelope with CommunicationMembershipOut.",),
            examples=(
                OperationExample(
                    title="Register bot channel membership",
                    arguments={
                        "project_id": 1,
                        "surface_ref": "slack-channel:C123",
                        "member_ref": "communication-profile:support",
                        "provider_key": "slack-bot",
                        "permissions": {"can_read": True, "can_write": True},
                    },
                ),
            ),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="communicationMembership.list",
            summary="List communication memberships and permission state.",
            input_model=CommunicationMembershipListInput,
            output_model=Page[CommunicationMembershipOut],
            handler=communication_membership_list,
            surfaces=_surfaces(
                "communicationMembership.list",
                "ops call communicationMembership.list",
            ),
            purpose="Use this to inspect where profiles, contacts, or bots can read/write.",
            prerequisites=("Pass project_id and optional surface/member/provider filters.",),
            returns=("A Page of CommunicationMembershipOut records.",),
            examples=(OperationExample(title="List memberships", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationTarget.upsert",
            summary="Create or update a named communication target.",
            input_model=CommunicationTargetUpsertInput,
            output_model=WriteEnvelope[CommunicationTargetOut],
            handler=communication_target_upsert,
            surfaces=_surfaces("communicationTarget.upsert", "ops call communicationTarget.upsert"),
            purpose=(
                "Use this setup operation to create named destinations such as internal-support "
                "or sergey-dm. Targets resolve to explicit provider action refs; they do not "
                "send messages or choose business behavior."
            ),
            prerequisites=("Pass provider_key and surface_ref.",),
            returns=("A WriteEnvelope with CommunicationTargetOut.",),
            examples=(
                OperationExample(
                    title="Register internal support target",
                    arguments={
                        "project_id": 1,
                        "key": "internal-support",
                        "provider_key": "slack-bot",
                        "surface_ref": "slack-channel:C123",
                    },
                ),
            ),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="communicationTarget.list",
            summary="List named communication targets.",
            input_model=CommunicationTargetListInput,
            output_model=Page[CommunicationTargetOut],
            handler=communication_target_list,
            surfaces=_surfaces("communicationTarget.list", "ops call communicationTarget.list"),
            purpose="Use this to discover configured safe send destinations.",
            prerequisites=("Pass project_id.",),
            returns=("A Page of CommunicationTargetOut records.",),
            examples=(OperationExample(title="List targets", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationTarget.resolve",
            summary="Resolve one named communication target to an explicit provider action.",
            input_model=CommunicationTargetResolveInput,
            output_model=CommunicationTargetResolveOut,
            handler=communication_target_resolve,
            surfaces=_surfaces(
                "communicationTarget.resolve",
                "ops call communicationTarget.resolve",
            ),
            purpose=(
                "Use this before sending across channels/providers. It applies static target "
                "policy and returns the provider action ref/defaults an agent can validate "
                "and execute with action.run or action.execute."
            ),
            prerequisites=("Create a communicationTarget first.",),
            returns=("Allowed/denied status plus explicit provider action ref and safe defaults.",),
            examples=(
                OperationExample(
                    title="Resolve internal support target",
                    arguments={
                        "project_id": 1,
                        "key": "internal-support",
                        "profile_ref": "communication-profile:support",
                    },
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationRoute.upsert",
            summary="Create or update a static communication handoff route.",
            input_model=CommunicationRouteUpsertInput,
            output_model=WriteEnvelope[CommunicationRouteOut],
            handler=communication_route_upsert,
            surfaces=_surfaces("communicationRoute.upsert", "ops call communicationRoute.upsert"),
            purpose=(
                "Use this to declare allowed cross-surface handoffs such as a customer "
                "Telegram issue to an internal Slack target. It never sends messages."
            ),
            prerequisites=("Pass source surfaces and target refs explicitly.",),
            returns=("A WriteEnvelope with CommunicationRouteOut.",),
            examples=(
                OperationExample(
                    title="Create customer issue route",
                    arguments={
                        "project_id": 1,
                        "key": "customer-issue-to-internal-support",
                        "source_surface_refs": ["telegram-chat:-1001"],
                        "target_refs": ["communication-target:internal-support"],
                    },
                ),
            ),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="communicationRoute.list",
            summary="List static communication handoff routes.",
            input_model=CommunicationRouteListInput,
            output_model=Page[CommunicationRouteOut],
            handler=communication_route_list,
            surfaces=_surfaces("communicationRoute.list", "ops call communicationRoute.list"),
            purpose="Use this to inspect configured cross-surface handoff routes.",
            prerequisites=("Pass project_id and optional source/target/profile filters.",),
            returns=("A Page of CommunicationRouteOut records.",),
            examples=(OperationExample(title="List routes", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationContext.query",
            summary="Query bounded stored communication history.",
            input_model=CommunicationContextQueryInput,
            output_model=CommunicationContextQueryOut,
            handler=communication_context_query,
            surfaces=_surfaces("communicationContext.query", "ops call communicationContext.query"),
            purpose=(
                "Use this when an agent needs recent stored conversation context before "
                "deciding a workflow. It never fetches live provider history."
            ),
            prerequisites=(
                "Pass bounded filters such as surface_ref, thread_ref, provider_key, or profile.",
                "Request only supported safe fields.",
            ),
            returns=("A compact list of selected message fields from stored StackOS records.",),
            examples=(
                OperationExample(
                    title="Read recent stored channel context",
                    arguments={
                        "project_id": 1,
                        "surface_ref": "slack-channel:C123",
                        "limit": 25,
                        "fields": ["message_ref", "sender_ref", "text_preview"],
                    },
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
    ]
