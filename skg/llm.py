from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from google import genai as google_genai
from google.genai.errors import APIError as GoogleAPIError
from pydantic import BaseModel, TypeAdapter

from . import config

logger = logging.getLogger(__name__)


class APIError(GoogleAPIError):
    """Custom API exception inheriting from Google's APIError to enable unified catching."""
    def __init__(self, message: str, code: int | None = None):
        self.code = code
        self.message = message
        # Google's APIError expects code and response_json
        super().__init__(code=code or 0, response_json={"error": {"message": message}})

    def __str__(self) -> str:
        return f"{self.message} (code: {self.code})"


class OpenRouterResponse:
    """Mocked response class matching Google GenAI response object."""
    def __init__(self, text: str, response_schema: Any = None):
        self.text = text
        self._response_schema = response_schema

    @property
    def parsed(self) -> Any:
        if not self._response_schema:
            return None

        clean_text = self.text.strip()
        
        # Extract the JSON block (array or object) from clean_text
        # Look for first '[' or '{' and matching last ']' or '}'
        start_arr = clean_text.find('[')
        start_obj = clean_text.find('{')
        
        if start_arr != -1 and (start_obj == -1 or start_arr < start_obj):
            end_arr = clean_text.rfind(']')
            if end_arr != -1:
                clean_text = clean_text[start_arr:end_arr+1]
        elif start_obj != -1:
            end_obj = clean_text.rfind('}')
            if end_obj != -1:
                clean_text = clean_text[start_obj:end_obj+1]
        else:
            # Strip markdown codeblock lines if present
            if clean_text.startswith("```"):
                lines = clean_text.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                clean_text = "\n".join(lines).strip()

        try:
            if isinstance(self._response_schema, type) and issubclass(self._response_schema, BaseModel):
                return self._response_schema.model_validate_json(clean_text)
            else:
                return TypeAdapter(self._response_schema).validate_json(clean_text)
        except Exception as e:
            logger.error("Failed to parse JSON schema: %s. Raw text: %r, extracted: %r", e, self.text, clean_text)
            # Re-raise as ValueError to match validation expectations
            raise ValueError(f"Failed to parse LLM response into schema: {e}")


class OpenRouterModels:
    """Handles synchronous calls to OpenRouter."""
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url

    def generate_content(
        self, model: str, contents: str, config: dict | None = None
    ) -> OpenRouterResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        response_schema = None
        payload_format = None
        if config:
            response_schema = config.get("response_schema")
            is_base_model = isinstance(response_schema, type) and issubclass(response_schema, BaseModel)
            
            if config.get("response_mime_type") == "application/json" or response_schema:
                if is_base_model:
                    payload_format = {"type": "json_object"}
                if "json" not in contents.lower():
                    contents += "\nReturn a valid JSON response matching the requested schema."

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": contents}],
            "provider": {
                "order": ["DeepInfra"],
                "allow_fallbacks": True,
            },
            "reasoning": {
                "effort": "xhigh",
            },
        }
        if payload_format:
            payload["response_format"] = payload_format

        try:
            with httpx.Client() as client:
                r = client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=200.0,
                )
                r.raise_for_status()
                res_json = r.json()
                
                if "error" in res_json:
                    err = res_json["error"]
                    code = err.get("code")
                    try:
                        code_int = int(code) if code is not None else None
                    except ValueError:
                        code_int = None
                    raise APIError(message=err.get("message", "Unknown OpenRouter error"), code=code_int)
                
                if "choices" not in res_json or not res_json["choices"]:
                    raise APIError(message=f"Invalid response format from OpenRouter: {res_json}", code=None)
                
                text = res_json["choices"][0]["message"]["content"]
                return OpenRouterResponse(text, response_schema)
        except httpx.HTTPStatusError as e:
            raise APIError(message=str(e), code=e.response.status_code)
        except httpx.HTTPError as e:
            raise APIError(message=str(e), code=None)


class AsyncOpenRouterModels:
    """Handles asynchronous calls to OpenRouter with concurrency control."""
    def __init__(self, api_key: str, base_url: str, max_concurrency: int = 3):
        self.api_key = api_key
        self.base_url = base_url
        self.semaphore = asyncio.Semaphore(max_concurrency)

    async def generate_content(
        self, model: str, contents: str, config: dict | None = None
    ) -> OpenRouterResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        response_schema = None
        payload_format = None
        if config:
            response_schema = config.get("response_schema")
            is_base_model = isinstance(response_schema, type) and issubclass(response_schema, BaseModel)
            
            if config.get("response_mime_type") == "application/json" or response_schema:
                if is_base_model:
                    payload_format = {"type": "json_object"}
                if "json" not in contents.lower():
                    contents += "\nReturn a valid JSON response matching the requested schema."

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": contents}],
            "provider": {
                "order": ["DeepInfra"],
                "allow_fallbacks": True,
            },
            "reasoning": {
                "effort": "xhigh",
            },
        }
        if payload_format:
            payload["response_format"] = payload_format

        try:
            async with self.semaphore:
                async with httpx.AsyncClient() as client:
                    r = await client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                        timeout=200.0,
                    )
                    r.raise_for_status()
                    res_json = r.json()
                    
                    if "error" in res_json:
                        err = res_json["error"]
                        code = err.get("code")
                        try:
                            code_int = int(code) if code is not None else None
                        except ValueError:
                            code_int = None
                        raise APIError(message=err.get("message", "Unknown OpenRouter error"), code=code_int)
                    
                    if "choices" not in res_json or not res_json["choices"]:
                        raise APIError(message=f"Invalid response format from OpenRouter: {res_json}", code=None)
                    
                    text = res_json["choices"][0]["message"]["content"]
                    return OpenRouterResponse(text, response_schema)
        except httpx.HTTPStatusError as e:
            raise APIError(message=str(e), code=e.response.status_code)
        except httpx.HTTPError as e:
            raise APIError(message=str(e), code=None)


class AsyncOpenRouterAio:
    """Async namespace helper for client.aio."""
    def __init__(self, api_key: str, base_url: str, max_concurrency: int = 3):
        self.models = AsyncOpenRouterModels(api_key, base_url, max_concurrency)


class OpenRouterClient:
    """Main OpenRouter client mimicking the google-genai Client structure."""
    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1", max_concurrency: int = 3):
        if not api_key:
            raise ValueError("OpenRouter API key is required.")
        self.models = OpenRouterModels(api_key, base_url)
        self.aio = AsyncOpenRouterAio(api_key, base_url, max_concurrency)


# --- Unified Provider Dispatch Helpers ---

def get_client() -> Any:
    """Instantiate the configured LLM client depending on ACTIVE_PROVIDER."""
    if config.ACTIVE_PROVIDER == "GEMINI":
        if not config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY environment variable is not set.")
        return google_genai.Client(api_key=config.GEMINI_API_KEY)
    elif config.ACTIVE_PROVIDER == "OPENROUTER":
        if not config.OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY environment variable is not set.")
        return OpenRouterClient(api_key=config.OPENROUTER_API_KEY)
    else:
        raise ValueError(f"Unknown ACTIVE_PROVIDER: {config.ACTIVE_PROVIDER}")


def get_model() -> str:
    """Get the configured LLM model depending on ACTIVE_PROVIDER."""
    if config.ACTIVE_PROVIDER == "GEMINI":
        return config.GEMINI_MODEL
    elif config.ACTIVE_PROVIDER == "OPENROUTER":
        return config.OPENROUTER_MODEL
    else:
        raise ValueError(f"Unknown ACTIVE_PROVIDER: {config.ACTIVE_PROVIDER}")


def get_timeout() -> float:
    """Get the configured request timeout depending on ACTIVE_PROVIDER."""
    if config.ACTIVE_PROVIDER == "GEMINI":
        return config.GEMINI_REQUEST_TIMEOUT
    elif config.ACTIVE_PROVIDER == "OPENROUTER":
        return config.OPENROUTER_REQUEST_TIMEOUT
    else:
        raise ValueError(f"Unknown ACTIVE_PROVIDER: {config.ACTIVE_PROVIDER}")
