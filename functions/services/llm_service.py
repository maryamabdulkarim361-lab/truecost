"""LLM service for TrueCost.

Provides LangChain integration for OpenAI and Gemini-compatible Deep Agents.
"""

from typing import Dict, Any, Optional, List
import asyncio
import httpx
from openai import DefaultAsyncHttpxClient
import structlog
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from config.settings import settings
from config.errors import StructuredError, ErrorCode
from services.llm_errors import classify_llm_error
from services.local_provider_budget import transport_options

logger = structlog.get_logger()


class _DeferredAsyncClient:
    """Delay async model construction until an async API is actually used."""

    def __init__(self, resolve):
        self._resolve = resolve

    def __getattr__(self, name):
        return getattr(self._resolve(), name)


class LLMService:
    """Service for LLM operations using LangChain.
    
    Provides a wrapper around ChatOpenAI with token tracking
    and error handling.

    Own with ``async with service`` for the duration of an async request,
    or await ``aclose()`` before closing the request's event loop.
    """
    
    def __init__(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        api_key: Optional[str] = None
    ):
        """Initialize LLMService.
        
        Args:
            model: Model name (default from settings).
            temperature: Temperature (default from settings).
            api_key: Selected provider's API key (default from settings).
        """
        self.model = model or settings.llm_model
        self.provider = settings.llm_provider
        self.temperature = temperature or settings.llm_temperature
        self.api_key = api_key or settings.llm_api_key
        if not self.api_key:
            raise ValueError(f"{settings.llm_key_env} is required for the selected LLM provider")
        self._provider_options = settings.llm_client_options
        
        self._client: Optional[ChatOpenAI] = None
        self._http_async_client: Optional[httpx.AsyncClient] = None
        self._owner_loop = None
        self._generation = 0
        self._closing = False
        self._total_tokens_used = 0

    async def __aenter__(self):
        self._validate_loop()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        try:
            await self.aclose()
        except Exception:
            if exc_value is None:
                raise
            # Preserve the active generation exception, without logging secrets.

    def _validate_loop(self):
        if self._closing:
            raise RuntimeError("LLMService transport cleanup is in progress")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._owner_loop is not None and self._owner_loop is not loop:
            raise RuntimeError("LLMService must be closed before changing event loops")
        return loop

    def _get_http_async_client(self) -> httpx.AsyncClient:
        """Own the transport within the current request's event loop."""
        loop = self._validate_loop()
        if loop is None:
            raise RuntimeError("Async LLM transport requires a running event loop")
        if self._http_async_client is None:
            self._http_async_client = DefaultAsyncHttpxClient(**transport_options())
            self._owner_loop = loop
        return self._http_async_client

    async def aclose(self) -> None:
        """Close on the owning loop before its request finishes."""
        self._validate_loop()
        transport = self._http_async_client
        self._closing = True
        try:
            if transport is not None:
                await transport.aclose()
        finally:
            self._http_async_client = None
            self._owner_loop = None
            self._client = None
            self._generation += 1
            self._closing = False

    def _create_async_model(self, options):
        model = ChatOpenAI(
            **options, http_async_client=self._get_http_async_client()
        )
        generation = self._generation

        def checked(client):
            self._validate_loop()
            if generation != self._generation:
                raise RuntimeError("LLM model belongs to a closed service lifetime")
            return client

        # Guard retained model references as well as service.client access.
        client, root = model.async_client, model.root_async_client
        model.async_client = _DeferredAsyncClient(lambda: checked(client))
        model.root_async_client = _DeferredAsyncClient(lambda: checked(root))
        return model

    def _create_model(self, model, temperature):
        loop = self._validate_loop()
        options = dict(model=model, temperature=temperature, api_key=self.api_key,
                       max_retries=0,
                       **self._provider_options)
        if loop is not None:
            return self._create_async_model(options)

        # Keep synchronous construction/invoke usable without allocating an async
        # transport. Supplying both proxies bypasses LangChain's default cache.
        generation = self._generation
        resolved = None

        def resolve():
            nonlocal resolved
            self._validate_loop()
            if generation != self._generation:
                raise RuntimeError("LLM model belongs to a closed service lifetime")
            if resolved is None:
                resolved = self._create_async_model(options)
            return resolved

        return ChatOpenAI(
            **options,
            async_client=_DeferredAsyncClient(lambda: resolve().async_client),
            root_async_client=_DeferredAsyncClient(lambda: resolve().root_async_client),
        )
    
    @property
    def client(self) -> ChatOpenAI:
        """Get LangChain ChatOpenAI client (lazy initialization)."""
        self._validate_loop()
        if self._client is None:
            self._client = self._create_model(self.model, self.temperature)
        return self._client
    
    @property
    def total_tokens_used(self) -> int:
        """Get total tokens used across all calls."""
        return self._total_tokens_used
    
    async def generate(
        self,
        messages: List[BaseMessage],
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """Generate a response from the LLM.
        
        Args:
            messages: List of LangChain messages.
            max_tokens: Optional max tokens for response.
            
        Returns:
            Dict with content and token usage.
            
        Raises:
            TrueCostError: If LLM call fails.
        """
        failure_stage = "client_initialization"
        try:
            kwargs = {}
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            
            client = self.client
            failure_stage = "provider_request"
            self._completion_status = None
            response = await client.ainvoke(messages, **kwargs)
            failure_stage = "response_processing"
            
            metadata = getattr(response, "response_metadata", None)
            finish = metadata.get("finish_reason") if isinstance(metadata, dict) else None
            if isinstance(finish, str) and finish in ("stop", "length", "content_filter", "tool_calls", "function_call"):
                self._completion_status = finish
            content = getattr(response, "content", None)
            if not isinstance(content, str):
                raise StructuredError(ErrorCode.LLM_INVALID_RESPONSE,
                                      provider=self.provider, model=self.model,
                                      reason="empty_response" if content is None else "invalid_json_type",
                                      field_path="response", failure_stage="response_processing")
            # Prefer normalized LangChain usage, with compatibility fallback.
            tokens_used = 0
            usage = getattr(response, "usage_metadata", None)
            if not isinstance(usage, dict):
                usage = (getattr(response, "response_metadata", None) or {}).get("token_usage") or {}
            tokens_used = int(usage.get("total_tokens") or 0)
            self._total_tokens_used += tokens_used
            
            logger.info(
                "llm_generated",
                model=self.model,
                tokens_used=tokens_used,
                content_length=len(response.content)
            )
            
            return {
                "content": response.content,
                "tokens_used": tokens_used
            }
            
        except Exception as e:
            raise classify_llm_error(e, provider=self.provider, model=self.model,
                                     failure_stage=failure_stage) from None

    async def generate_with_system_prompt(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """Generate a response with system prompt.
        
        Args:
            system_prompt: System prompt for context.
            user_message: User message/query.
            max_tokens: Optional max tokens for response.
            
        Returns:
            Dict with content and token usage.
        """
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message)
        ]
        return await self.generate(messages, max_tokens)
    
    async def generate_json(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """Generate a JSON response.
        
        Adds JSON formatting instructions to the system prompt.
        
        Args:
            system_prompt: System prompt for context.
            user_message: User message/query.
            max_tokens: Optional max tokens for response.
            
        Returns:
            Dict with parsed JSON content and token usage.
            
        Raises:
            TrueCostError: If response is not valid JSON.
        """
        import json
        
        json_prompt = f"""{system_prompt}

IMPORTANT: You MUST respond with valid JSON only. No markdown, no explanation, just JSON."""
        
        self._completion_status = None
        result = await self.generate_with_system_prompt(
            json_prompt,
            user_message,
            max_tokens
        )
        
        def invalid(reason):
            raise StructuredError(
                ErrorCode.LLM_INVALID_RESPONSE, provider=self.provider, model=self.model,
                reason=reason, field_path="response", failure_stage="response_processing",
                completion_status=getattr(self, "_completion_status", None),
            ) from None

        content = result.get("content")
        if content is None or (isinstance(content, str) and not content.strip()):
            invalid("empty_response")
        if not isinstance(content, str):
            invalid("invalid_json_type")
        content = content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if (len(lines) < 3 or lines[0] not in ("```", "```json")
                    or lines[-1] != "```" or any("```" in line for line in lines[1:-1])):
                invalid("invalid_json")
            content = "\n".join(lines[1:-1]).strip()
            if not content:
                invalid("empty_response")
        try:
            def reject_constant(value):
                raise ValueError("Nonstandard JSON constant")
            parsed = json.loads(content, parse_constant=reject_constant)
        except (ValueError, RecursionError):
            invalid("invalid_json")
        if not isinstance(parsed, dict):
            invalid("invalid_json_type")
        return {"content": parsed, "tokens_used": result["tokens_used"]}

    def create_chat_model(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None
    ) -> ChatOpenAI:
        """Create a new ChatOpenAI instance.
        
        Useful for creating models with different configurations
        for different agents.

        Synchronous construction and invocation need no event loop. For async
        invocation, use ``async with service`` (or await ``service.aclose()``
        on the same loop). Returned models must not escape that lifetime.
        
        Args:
            model: Model name (default from settings).
            temperature: Temperature (default from settings).
            
        Returns:
            Configured ChatOpenAI instance.
        """
        return self._create_model(
            model or self.model,
            temperature if temperature is not None else self.temperature,
        )
