"""A thin wrapper over the OpenAI chat completions API.

Deliberately thin. The model is asked for JSON matching a schema and nothing
else; no retries, no repair of malformed output, no fallback model. A call
that fails is reported as a failure rather than smoothed over, because the
count of failures is information the user is meant to see.

`temperature` is never sent: the model id is supplied by the user through an
environment variable, and some models reject the parameter outright.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

MODEL_ENV_VAR = "OPENAI_MODEL"
API_KEY_ENV_VAR = "OPENAI_API_KEY"


class ConfigError(Exception):
    """Raised when the environment is not set up to call the model."""


@dataclass(frozen=True)
class Usage:
    """Token counts reported by the API for a single call."""

    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class ModelError(Exception):
    """Raised when a model call fails or returns unusable output.

    Carries whatever usage the API reported. A response that arrives and is
    then rejected has already been billed, and a tool that reports token
    counts has no business quietly leaving those tokens out of the total.
    """

    def __init__(self, message: str, usage: "Usage | None" = None):
        super().__init__(message)
        self.usage = usage or Usage()


def resolve_model(explicit: str | None = None) -> str:
    """Return the model id to use, or explain how to set one."""
    model = explicit or os.environ.get(MODEL_ENV_VAR, "").strip()
    if not model:
        raise ConfigError(
            f"No model set. Export {MODEL_ENV_VAR} with the OpenAI model id you "
            f'want to use, for example:\n\n    export {MODEL_ENV_VAR}="<model-id>"\n\n'
            "There is no default on purpose: a hard-coded model id goes stale, "
            "and the choice of model belongs in the record of the run."
        )
    return model


class Client:
    """Calls one model with one schema at a time."""

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = resolve_model(model)

        key = api_key or os.environ.get(API_KEY_ENV_VAR, "").strip()
        if not key:
            raise ConfigError(
                f"No API key found. Export {API_KEY_ENV_VAR} with your OpenAI key."
            )

        try:
            from openai import OpenAI
        except ImportError as error:  # pragma: no cover - dependency is declared
            raise ConfigError(
                "The `openai` package is not installed. Run `uv sync`."
            ) from error

        self._client = OpenAI(api_key=key)

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict,
        schema_name: str,
    ) -> tuple[dict, Usage]:
        """Send one request and return the parsed JSON object and token usage."""
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    },
                },
            )
        except Exception as error:
            raise ModelError(f"{type(error).__name__}: {error}") from error

        # Read usage first: every rejection below happens after the tokens
        # have been spent, and the caller is told what the run cost.
        usage = Usage()
        reported = getattr(response, "usage", None)
        if reported is not None:
            usage = Usage(
                input_tokens=reported.prompt_tokens or 0,
                output_tokens=reported.completion_tokens or 0,
            )

        choices = getattr(response, "choices", None) or []
        if not choices:
            raise ModelError("The model returned no choices.", usage)

        choice = choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise ModelError(
                "The model stopped because it hit its output limit, so the "
                "response is incomplete.",
                usage,
            )

        content = getattr(getattr(choice, "message", None), "content", None)
        if not content:
            raise ModelError("The model returned an empty response.", usage)

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as error:
            raise ModelError(
                f"The model returned text that is not JSON: {error}", usage
            ) from error

        # The schema is sent with strict=True, so this should not happen. It is
        # checked anyway because the alternative is an AttributeError deep in
        # the caller, which would take down a whole labelling run.
        if not isinstance(parsed, dict):
            raise ModelError(
                f"The model returned a JSON {type(parsed).__name__} where an "
                "object was expected.",
                usage,
            )

        return parsed, usage
