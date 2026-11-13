"""Shared installation-wide catalog contracts. No project scope or content policy."""

from pydantic import Field, model_validator

from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.errors import ToolNotGrantedError
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import OperationExample, OperationResponsePolicy
from stackos.repositories.patterns import (
    PatternContent,
    PatternDetail,
    PatternPage,
    PatternRepository,
    PatternSource,
    PatternSummary,
)


class PatternListInput(MCPInput):
    query: str | None = Field(default=None, max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=50)
    source: PatternSource = "all"
    limit: int = Field(default=25, ge=1, le=100)
    cursor: int | None = Field(default=None, ge=1)


class PatternGetInput(MCPInput):
    pattern_id: int | None = Field(default=None, ge=1)
    key: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def one_identity(self):
        if (self.pattern_id is None) == (self.key is None):
            raise ValueError("Supply exactly one of pattern_id or key")
        return self


class PatternUpsertInput(MCPInput, PatternContent):
    pass


class PatternDeleteInput(MCPInput):
    pattern_id: int = Field(ge=1)
    expected_etag: str = Field(min_length=1)


async def _list(inp, ctx, _emitter):
    return PatternRepository(ctx.session).list(
        **inp.model_dump(
            include={
                "query",
                "tags",
                "source",
                "limit",
                "cursor",
            }
        )
    )


async def _get(inp, ctx, _emitter):
    return PatternRepository(ctx.session).get(pattern_id=inp.pattern_id, key=inp.key)


def _require_direct_owner(ctx):
    if ctx.run_token is not None:
        raise ToolNotGrantedError(
            "A project run grant does not authorize global catalog mutation",
            data={
                "next_action": "Use an authenticated direct owner session without a run token.",
            },
        )


async def _upsert(inp, ctx, _emitter):
    _require_direct_owner(ctx)
    row = PatternRepository(ctx.session).upsert(
        **inp.model_dump(include=set(PatternContent.model_fields)),
        expected_etag=inp.expected_etag,
    )
    return WriteEnvelope(data=PatternSummary(**row.model_dump(exclude={"body"})))


async def _delete(inp, ctx, _emitter):
    _require_direct_owner(ctx)
    row = PatternRepository(ctx.session).delete(inp.pattern_id, expected_etag=inp.expected_etag)
    return WriteEnvelope(data=row)


def operation_specs():
    examples = {
        "list": OperationExample(
            title="Find relevant guidance",
            arguments={
                "query": "testing",
                "tags": ["python"],
                "source": "custom",
                "limit": 10,
            },
        ),
        "get": OperationExample(title="Read the selected entry", arguments={"pattern_id": 1}),
        "upsert": OperationExample(
            title="Create an illustrative entry",
            arguments={
                "key": "example-guidance",
                "title": "Example guidance",
                "summary": "Applicability",
                "body": "Recommendations, examples, technology references and usage instructions.",
                "tags": ["freeform"],
            },
        ),
        "delete": OperationExample(
            title="Delete a selected entry at its current revision",
            arguments={"pattern_id": 1, "expected_etag": "1"},
        ),
    }
    definitions = [
        (
            "list",
            PatternListInput,
            PatternPage,
            _list,
            "Search bounded pattern summaries using combined text, any-tag and source filters.",
        ),
        (
            "get",
            PatternGetInput,
            PatternDetail,
            _get,
            "Read one full Markdown pattern by ID or key.",
        ),
        (
            "upsert",
            PatternUpsertInput,
            WriteEnvelope[PatternSummary],
            _upsert,
            "Create by stable key or save a pattern using its current expected_etag.",
        ),
        (
            "delete",
            PatternDeleteInput,
            WriteEnvelope[PatternSummary],
            _delete,
            "Delete one pattern using its current expected_etag.",
        ),
    ]
    return [
        operation_spec(
            name=f"pattern.{verb}",
            summary=summary,
            input_model=inp,
            output_model=out,
            handler=handler,
            purpose=summary + " The catalog is shared by all projects in this local installation.",
            mutating=verb in {"upsert", "delete"},
            grant_policy="direct-read" if verb in {"list", "get"} else "direct-write",
            category="resources",
            browser_safe=True,
            when_to_use=(summary,),
            prerequisites=()
            if verb in {"list", "get"}
            else ("Use an established authenticated owner session without a project run token.",),
            returns=(
                "Lists and write receipts omit bodies; raw pattern.get returns one full entry.",
                "Source fields are server-derived. Normal writes cannot set bundled metadata.",
                "Conflicts preserve etags and repair context. Reset paging after filter changes.",
            ),
            examples=(examples[verb],),
            response_policy=OperationResponsePolicy(
                default_mode="raw" if verb == "get" else "compact",
                allowed_modes=("compact", "raw"),
                compact_notes=(
                    "Keep identities, revisions, etags, provenance and pagination; omit body.",
                ),
            ),
        )
        for verb, inp, out, handler, summary in definitions
    ]
