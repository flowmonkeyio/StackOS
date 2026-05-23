"""Result envelope shapes for the REST surface.

Per PLAN.md L754-L763: mutating endpoints return ``{data, run_id, project_id}``;
read endpoints return ``T`` bare. We expose ``WriteResponse[T]`` here as a
generic pydantic model so each route's ``response_model`` can carry the
proper schema into OpenAPI (and from there into ``ui/src/api.ts`` via the
codegen pass).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from stackos.repositories.base import Envelope


class WriteResponse[T](BaseModel):
    """Wire shape for mutating endpoints.

    PLAN.md L758-L763 specifies the envelope and the verb-prefix list that
    triggers it (``create|update|set|mark|...``); for REST we apply it on
    every POST/PUT/PATCH/DELETE explicitly via the ``response_model`` (or
    ``write_response`` helper below).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"data": None, "run_id": None, "project_id": 1},
        }
    )

    data: T
    run_id: int | None = None
    project_id: int | None = None


def write_response[T](envelope: Envelope[T]) -> WriteResponse[T]:
    """Convert a repository ``Envelope[T]`` into a wire ``WriteResponse[T]``.

    The repository layer never knows it's being called over REST (vs. MCP
    in M3); this thin adapter is the only place the wire shape is applied.
    """
    return WriteResponse[T](
        data=envelope.data,
        run_id=envelope.run_id,
        project_id=envelope.project_id,
    )


__all__ = ["WriteResponse", "write_response"]
