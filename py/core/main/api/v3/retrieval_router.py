import logging
import textwrap
from typing import Any, Optional
from uuid import UUID

from fastapi import Body, Depends
from fastapi.responses import StreamingResponse

from core.base import (
    GenerationConfig,
    Message,
    R2RException,
    SearchMode,
    SearchSettings,
    select_search_filters,
)
from core.base.api.models import (
    WrappedAgentResponse,
    WrappedCompletionResponse,
    WrappedEmbeddingResponse,
    WrappedLLMChatCompletion,
    WrappedRAGResponse,
    WrappedSearchResponse,
)

from ...abstractions import R2RProviders, R2RServices
from ...config import R2RConfig
from .base_router import BaseRouterV3


def merge_search_settings(
    base: SearchSettings, overrides: SearchSettings
) -> SearchSettings:
    # Convert both to dict
    base_dict = base.model_dump()
    overrides_dict = overrides.model_dump(exclude_unset=True)

    # Update base_dict with values from overrides_dict
    # This ensures that any field set in overrides takes precedence
    for k, v in overrides_dict.items():
        base_dict[k] = v

    # Construct a new SearchSettings from the merged dict
    return SearchSettings(**base_dict)


class RetrievalRouter(BaseRouterV3):
    def __init__(
        self, providers: R2RProviders, services: R2RServices, config: R2RConfig
    ):
        logging.info("Initializing RetrievalRouter")
        super().__init__(providers, services, config)

    def _register_workflows(self):
        pass

    def _prepare_search_settings(
        self,
        auth_user: Any,
        search_mode: SearchMode,
        search_settings: Optional[SearchSettings],
    ) -> SearchSettings:
        """Prepare the effective search settings based on the provided
        search_mode, optional user-overrides in search_settings, and applied
        filters."""
        if search_mode != SearchMode.custom:
            # Start from mode defaults
            effective_settings = SearchSettings.get_default(search_mode.value)
            if search_settings:
                # Merge user-provided overrides
                effective_settings = merge_search_settings(
                    effective_settings, search_settings
                )
        else:
            # Custom mode: use provided settings or defaults
            effective_settings = search_settings or SearchSettings()

        # Apply user-specific filters
        effective_settings.filters = select_search_filters(
            auth_user, effective_settings
        )
        return effective_settings

    def _setup_routes(self):
        @self.router.post(
            "/retrieval/search",
            dependencies=[Depends(self.rate_limit_dependency)],
            summary="Search R2R",
            openapi_extra={
                "x-codeSamples": [
                    {
                        "lang": "Python",
                        "source": textwrap.dedent("""
                            from r2r import R2RClient

                            client = R2RClient()
                            # if using auth, do client.login(...)

                            # Basic mode, no overrides
                            response = client.retrieval.search(
                                query="Who is Aristotle?",
                                search_mode="basic"
                            )

                            # Advanced mode with overrides
                            response = client.retrieval.search(
                                query="Who is Aristotle?",
                                search_mode="advanced",
                                search_settings={
                                    "filters": {"document_id": {"$eq": "3e157b3a-..."}},
                                    "limit": 5
                                }
                            )

                            # Custom mode with full control
                            response = client.retrieval.search(
                                query="Who is Aristotle?",
                                search_mode="custom",
                                search_settings={
                                    "use_semantic_search": True,
                                    "filters": {"category": {"$like": "%philosophy%"}},
                                    "limit": 20,
                                    "chunk_settings": {"index_measure": "l2_distance"}
                                }
                            )
                            """),
                    },
                    {
                        "lang": "JavaScript",
                        "source": textwrap.dedent("""
                            const { r2rClient } = require("r2r-js");

                            const client = new r2rClient();

                            function main() {
                                const response = await client.search({
                                    query: "Who is Aristotle?",
                                    search_settings: {
                                        filters: {"document_id": {"$eq": "3e157b3a-8469-51db-90d9-52e7d896b49b"}},
                                        useSemanticSearch: true
                                    }
                                });
                            }

                            main();
                            """),
                    },
                    {
                        "lang": "Shell",
                        "source": textwrap.dedent("""
                            curl -X POST "https://api.example.com/retrieval/search" \\
                                -H "Content-Type: application/json" \\
                                -H "Authorization: Bearer YOUR_API_KEY" \\
                                -d '{
                                "query": "Who is Aristotle?",
                                "search_settings": {
                                    filters: {"document_id": {"$eq": "3e157b3a-8469-51db-90d9-52e7d896b49b"}},
                                    use_semantic_search: true
                                }
                            }'
                            """),
                    },
                ]
            },
        )
        @self.base_endpoint
        async def search_app(
            query: str = Body(
                ...,
                description="Search query to find relevant documents",
            ),
            search_mode: SearchMode = Body(
                default=SearchMode.custom,
                description=(
                    "Default value of `custom` allows full control over search settings.\n\n"
                    "Pre-configured search modes:\n"
                    "`basic`: A simple semantic-based search.\n"
                    "`advanced`: A more powerful hybrid search combining semantic and full-text.\n"
                    "`custom`: Full control via `search_settings`.\n\n"
                    "If `filters` or `limit` are provided alongside `basic` or `advanced`, "
                    "they will override the default settings for that mode."
                ),
            ),
            search_settings: Optional[SearchSettings] = Body(
                None,
                description=(
                    "The search configuration object. If `search_mode` is `custom`, "
                    "these settings are used as-is. For `basic` or `advanced`, these settings will override the default mode configuration.\n\n"
                    "Common overrides include `filters` to narrow results and `limit` to control how many results are returned."
                ),
            ),
            auth_user=Depends(self.providers.auth.auth_wrapper()),
        ) -> WrappedSearchResponse:
            """Perform a search query against vector and/or graph-based
            databases.

            **Search Modes:**
            - `basic`: Defaults to semantic search. Simple and easy to use.
            - `advanced`: Combines semantic search with full-text search for more comprehensive results.
            - `custom`: Complete control over how search is performed. Provide a full `SearchSettings` object.

            **Filters:**
            Apply filters directly inside `search_settings.filters`. For example:
            ```json
            {
              "filters": {"document_id": {"$eq": "3e157b3a-..."}}
            }
            ```
            Supported operators: `$eq`, `$neq`, `$gt`, `$gte`, `$lt`, `$lte`, `$like`, `$ilike`, `$in`, `$nin`.

            **Limit:**
            Control how many results you get by specifying `limit` inside `search_settings`. For example:
            ```json
            {
              "limit": 20
            }
            ```

            **Examples:**
            - Using `basic` mode and no overrides:
            Just specify `search_mode="basic"`.
            - Using `advanced` mode and applying a filter:
            Specify `search_mode="advanced"` and include `search_settings={"filters": {...}, "limit": 5}` to override defaults.
            - Using `custom` mode:
            Provide the entire `search_settings` to define your search exactly as you want it.
            """
            if query == "":
                raise R2RException("Query cannot be empty", 400)
            effective_settings = self._prepare_search_settings(
                auth_user, search_mode, search_settings
            )
            results = await self.services.retrieval.search(
                query=query,
                search_settings=effective_settings,
            )
            return results

        @self.router.post(
            "/retrieval/rag",
            dependencies=[Depends(self.rate_limit_dependency)],
            summary="RAG Query",
            openapi_extra={
                "x-codeSamples": [
                    {
                        "lang": "Python",
                        "source": textwrap.dedent("""
                            from r2r import R2RClient

                            client = R2RClient()
                            # when using auth, do client.login(...)

                            response = client.retrieval.rag(
                                query="Who is Aristotle?",
                                search_settings={
                                    "use_semantic_search": True,
                                    "filters": {"document_id": {"$eq": "3e157b3a-8469-51db-90d9-52e7d896b49b"}},
                                    "limit": 10,
                                    "chunk_settings": {
                                        "limit": 20, # separate limit for chunk vs. graph
                                    },
                                },
                                rag_generation_config={
                                    "stream": false,
                                    "temperature": 0.7,
                                    "max_tokens": 150
                                }
                            )
                            """),
                    },
                    {
                        "lang": "JavaScript",
                        "source": textwrap.dedent("""
                            const { r2rClient } = require("r2r-js");

                            const client = new r2rClient();

                            function main() {
                                const response = await client.retrieval.rag({
                                    query: "Who is Aristotle?",
                                    search_settings: {
                                        filters: {"document_id": {"$eq": "3e157b3a-8469-51db-90d9-52e7d896b49b"}},
                                        useSemanticSearch: true,
                                        chunkSettings: {
                                            limit: 20, # separate limit for chunk vs. graph
                                        },
                                    },
                                    ragGenerationConfig: {
                                        stream: false,
                                        temperature: 0.7,
                                        maxTokens: 150
                                    }
                                });
                            }

                            main();
                            """),
                    },
                    {
                        "lang": "Shell",
                        "source": textwrap.dedent("""
                            curl -X POST "https://api.example.com/retrieval/rag" \\
                                -H "Content-Type: application/json" \\
                                -H "Authorization: Bearer YOUR_API_KEY" \\
                                -d '{
                                "query": "Who is Aristotle?",
                                "search_settings": {
                                    "use_semantic_search": True,
                                    "filters": {"document_id": {"$eq": "3e157b3a-8469-51db-90d9-52e7d896b49b"}},
                                    "limit": 10,
                                    chunk_settings={
                                        "limit": 20, # separate limit for chunk vs. graph
                                    },
                                },
                                "rag_generation_config": {
                                    stream: false,
                                    temperature: 0.7,
                                    max_tokens: 150
                                }
                            }'
                            """),
                    },
                ]
            },
        )
        @self.base_endpoint
        async def rag_app(
            query: str = Body(...),
            search_mode: SearchMode = Body(
                default=SearchMode.custom,
                description=(
                    "Default value of `custom` allows full control over search settings.\n\n"
                    "Pre-configured search modes:\n"
                    "`basic`: A simple semantic-based search.\n"
                    "`advanced`: A more powerful hybrid search combining semantic and full-text.\n"
                    "`custom`: Full control via `search_settings`.\n\n"
                    "If `filters` or `limit` are provided alongside `basic` or `advanced`, "
                    "they will override the default settings for that mode."
                ),
            ),
            search_settings: Optional[SearchSettings] = Body(
                None,
                description=(
                    "The search configuration object. If `search_mode` is `custom`, "
                    "these settings are used as-is. For `basic` or `advanced`, these settings will override the default mode configuration.\n\n"
                    "Common overrides include `filters` to narrow results and `limit` to control how many results are returned."
                ),
            ),
            rag_generation_config: GenerationConfig = Body(
                default_factory=GenerationConfig,
                description="Configuration for RAG generation",
            ),
            task_prompt_override: Optional[str] = Body(
                default=None,
                description="Optional custom prompt to override default",
            ),
            include_title_if_available: bool = Body(
                default=False,
                description="Include document titles in responses when available",
            ),
            auth_user=Depends(self.providers.auth.auth_wrapper()),
        ) -> WrappedRAGResponse:
            """Execute a RAG (Retrieval-Augmented Generation) query.

            This endpoint combines search results with language model generation.
            It supports the same filtering capabilities as the search endpoint,
            allowing for precise control over the retrieved context.

            The generation process can be customized using the `rag_generation_config` parameter.
            """

            if "model" not in rag_generation_config.__fields_set__:
                rag_generation_config.model = self.config.app.quality_llm

            effective_settings = self._prepare_search_settings(
                auth_user, search_mode, search_settings
            )

            response = await self.services.retrieval.rag(
                query=query,
                search_settings=effective_settings,
                rag_generation_config=rag_generation_config,
                task_prompt_override=task_prompt_override,
                include_title_if_available=include_title_if_available,
            )

            if rag_generation_config.stream:

                async def stream_generator():
                    try:
                        async for chunk in response:
                            if len(chunk) > 1024:
                                for i in range(0, len(chunk), 1024):
                                    yield chunk[i : i + 1024]
                            else:
                                yield chunk
                    except GeneratorExit:
                        # Clean up if needed, then return
                        return

                return StreamingResponse(
                    stream_generator(), media_type="text/event-stream"
                )  # type: ignore
            else:
                return response

        @self.router.post(
            "/retrieval/agent",
            dependencies=[Depends(self.rate_limit_dependency)],
            summary="RAG-powered Conversational Agent",
            openapi_extra={
                "x-codeSamples": [
                    {
                        "lang": "Python",
                        "source": textwrap.dedent("""
                        from r2r import R2RClient

                        client = R2RClient()
                        # when using auth, do client.login(...)

                        response = client.retrieval.agent(
                            message={
                                "role": "user",
                                "content": "What were the key contributions of Aristotle to logic and how did they influence later philosophers?"
                            },
                            search_settings={
                                "use_semantic_search": True,
                                "filters": {"document_id": {"$eq": "3e157b3a-8469-51db-90d9-52e7d896b49b"}},
                                "limit": 10,
                                chunk_settings={
                                    "limit": 20, # separate limit for chunk vs. graph
                                },
                                graph_settings={
                                    "enabled": True,
                                },
                            },
                            rag_generation_config: {
                                stream: false,
                                temperature: 0.7,
                                max_tokens: 150
                            }
                            include_title_if_available=True,
                            conversation_id="550e8400-e29b-41d4-a716-446655440000"  # Optional for conversation continuity
                        )
                        """),
                    },
                    {
                        "lang": "JavaScript",
                        "source": textwrap.dedent("""
                            const { r2rClient } = require("r2r-js");

                            const client = new r2rClient();

                            function main() {
                                const response = await client.retrieval.agent({
                                    message: {
                                        role: "user",
                                        content: "What were the key contributions of Aristotle to logic and how did they influence later philosophers?"
                                    },
                                    searchSettings: {
                                        filters: {"document_id": {"$eq": "3e157b3a-8469-51db-90d9-52e7d896b49b"}},
                                        useSemanticSearch: true,
                                        chunkSettings: {
                                            limit: 20, # separate limit for chunk vs. graph
                                            enabled: true
                                        },
                                        graphSettings: {
                                            enabled: true,
                                        },
                                    },
                                    ragGenerationConfig: {
                                        stream: false,
                                        temperature: 0.7,
                                        maxTokens: 150
                                    },
                                    includeTitleIfAvailable: true,
                                    conversationId: "550e8400-e29b-41d4-a716-446655440000"
                                });
                            }

                            main();
                            """),
                    },
                    {
                        "lang": "Shell",
                        "source": textwrap.dedent("""
                            curl -X POST "https://api.example.com/retrieval/agent" \\
                                -H "Content-Type: application/json" \\
                                -H "Authorization: Bearer YOUR_API_KEY" \\
                                -d '{
                                "message": {
                                    "role": "user",
                                    "content": "What were the key contributions of Aristotle to logic and how did they influence later philosophers?"
                                },
                                "search_settings": {
                                    "use_semantic_search": True,
                                    "filters": {"document_id": {"$eq": "3e157b3a-8469-51db-90d9-52e7d896b49b"}},
                                    "limit": 10,
                                    chunk_settings={
                                        "limit": 20, # separate limit for chunk vs. graph
                                    },
                                    graph_settings={
                                        "enabled": True,
                                    },
                                },
                                "include_title_if_available": true,
                                "conversation_id": "550e8400-e29b-41d4-a716-446655440000"
                                }'
                            """),
                    },
                ]
            },
        )
        @self.base_endpoint
        async def agent_app(
            message: Optional[Message] = Body(
                None,
                description="Current message to process",
            ),
            messages: Optional[list[Message]] = Body(
                None,
                deprecated=True,
                description="List of messages (deprecated, use message instead)",
            ),
            search_mode: SearchMode = Body(
                default=SearchMode.custom,
                description=(
                    "Default value of `custom` allows full control over search settings.\n\n"
                    "Pre-configured search modes:\n"
                    "`basic`: A simple semantic-based search.\n"
                    "`advanced`: A more powerful hybrid search combining semantic and full-text.\n"
                    "`custom`: Full control via `search_settings`.\n\n"
                    "If `filters` or `limit` are provided alongside `basic` or `advanced`, "
                    "they will override the default settings for that mode."
                ),
            ),
            search_settings: Optional[SearchSettings] = Body(
                None,
                description=(
                    "The search configuration object. If `search_mode` is `custom`, "
                    "these settings are used as-is. For `basic` or `advanced`, these settings will override the default mode configuration.\n\n"
                    "Common overrides include `filters` to narrow results and `limit` to control how many results are returned."
                ),
            ),
            rag_generation_config: GenerationConfig = Body(
                default_factory=GenerationConfig,
                description="Configuration for RAG generation",
            ),
            task_prompt_override: Optional[str] = Body(
                default=None,
                description="Optional custom prompt to override default",
            ),
            include_title_if_available: bool = Body(
                default=True,
                description="Include document titles in responses when available",
            ),
            conversation_id: Optional[UUID] = Body(
                default=None,
                description="ID of the conversation",
            ),
            tools: Optional[list[str]] = Body(
                None,
                description="List of tools to execute",
            ),
            max_tool_context_length: Optional[int] = Body(
                default=32_768,
                description="Maximum length of returned tool context",
            ),
            use_system_context: Optional[bool] = Body(
                default=True,
                description="Use extended prompt for generation",
            ),
            auth_user=Depends(self.providers.auth.auth_wrapper()),
        ) -> WrappedAgentResponse:
            """Engage with an intelligent RAG-powered conversational agent for
            complex information retrieval and analysis.

            This advanced endpoint combines retrieval-augmented generation (RAG) with a conversational AI agent to provide
            detailed, context-aware responses based on your document collection. The agent can:

            - Maintain conversation context across multiple interactions
            - Dynamically search and retrieve relevant information from both vector and knowledge graph sources
            - Break down complex queries into sub-questions for comprehensive answers
            - Cite sources and provide evidence-based responses
            - Handle follow-up questions and clarifications
            - Navigate complex topics with multi-step reasoning

            Key Features:
            - Hybrid search combining vector and knowledge graph approaches
            - Contextual conversation management with conversation_id tracking
            - Customizable generation parameters for response style and length
            - Source document citation with optional title inclusion
            - Streaming support for real-time responses
            - Branch management for exploring different conversation paths

            Common Use Cases:
            - Research assistance and literature review
            - Document analysis and summarization
            - Technical support and troubleshooting
            - Educational Q&A and tutoring
            - Knowledge base exploration

            The agent uses both vector search and knowledge graph capabilities to find and synthesize
            information, providing detailed, factual responses with proper attribution to source documents.
            """
            if "model" not in rag_generation_config.__fields_set__:
                rag_generation_config.model = self.config.app.quality_llm

            effective_settings = self._prepare_search_settings(
                auth_user, search_mode, search_settings
            )

            try:
                response = await self.services.retrieval.agent(
                    message=message,
                    messages=messages,
                    search_settings=effective_settings,
                    rag_generation_config=rag_generation_config,
                    task_prompt_override=task_prompt_override,
                    include_title_if_available=include_title_if_available,
                    max_tool_context_length=max_tool_context_length,
                    conversation_id=(
                        str(conversation_id) if conversation_id else None
                    ),
                    use_system_context=use_system_context,
                    override_tools=tools,
                )

                if rag_generation_config.stream:

                    async def stream_generator():
                        try:
                            async for chunk in response:
                                if len(chunk) > 1024:
                                    for i in range(0, len(chunk), 1024):
                                        yield chunk[i : i + 1024]
                                else:
                                    yield chunk
                        except GeneratorExit:
                            # Clean up if needed, then return
                            return

                    return StreamingResponse(
                        stream_generator(), media_type="text/event-stream"
                    )  # type: ignore
                else:
                    return response
            except Exception as e:
                raise R2RException(str(e), 500) from e

        @self.router.post(
            "/retrieval/reasoning_agent",
            dependencies=[Depends(self.rate_limit_dependency)],
            summary="Reasoning Agent with RAG(Thoughts + Tools)",
            openapi_extra={
                "x-codeSamples": [
                    {
                        "lang": "Python",
                        "source": textwrap.dedent("""
                        from r2r import R2RClient

                        client = R2RClient()
                        # when using auth, do client.login(...)

                        response = client.retrieval.reasoning_agent(
                            message={
                                "role": "user",
                                "content": "What were the key contributions of Aristotle to logic and how did they influence later philosophers?"
                            },
                            rag_generation_config: {
                                stream: false,
                                temperature: 0.7,
                                max_tokens: 150
                            }
                            conversation_id="550e8400-e29b-41d4-a716-446655440000"  # Optional for conversation continuity
                        )
                        """),
                    },
                    {
                        "lang": "JavaScript",
                        "source": textwrap.dedent("""
                            const { r2rClient } = require("r2r-js");

                            const client = new r2rClient();

                            function main() {
                                const response = await client.retrieval.agent({
                                    message: {
                                        role: "user",
                                        content: "What were the key contributions of Aristotle to logic and how did they influence later philosophers?"
                                    },
                                    ragGenerationConfig: {
                                        stream: false,
                                        temperature: 0.7,
                                        maxTokens: 150
                                    },
                                    conversationId: "550e8400-e29b-41d4-a716-446655440000"
                                });
                            }

                            main();
                            """),
                    },
                    {
                        "lang": "Shell",
                        "source": textwrap.dedent("""
                            curl -X POST "https://api.example.com/retrieval/agent" \\
                                -H "Content-Type: application/json" \\
                                -H "Authorization: Bearer YOUR_API_KEY" \\
                                -d '{
                                "message": {
                                    "role": "user",
                                    "content": "What were the key contributions of Aristotle to logic and how did they influence later philosophers?"
                                },
                                "conversation_id": "550e8400-e29b-41d4-a716-446655440000"
                                }'
                            """),
                    },
                ]
            },
        )
        @self.base_endpoint
        async def reasoning_agent_app(
            message: Optional[Message] = Body(
                None,
                description="Current message to process",
            ),
            rag_generation_config: GenerationConfig = Body(
                default_factory=GenerationConfig,
                description="Configuration for RAG generation",
            ),
            conversation_id: Optional[UUID] = Body(
                default=None,
                description="ID of the conversation",
            ),
            tools: Optional[list[str]] = Body(
                None,
                description="List of tools to execute",
            ),
            max_tool_context_length: Optional[int] = Body(
                default=32_768,
                description="Maximum length of returned tool context",
            ),
            auth_user=Depends(self.providers.auth.auth_wrapper()),
        ) -> WrappedAgentResponse:
            """Engage with an intelligent RAG-powered conversational agent for
            complex information retrieval and analysis.

            This advanced endpoint combines retrieval-augmented generation (RAG) with a conversational AI agent to provide
            detailed, context-aware responses based on your document collection. The agent can:

            - Maintain conversation context across multiple interactions
            - Dynamically search and retrieve relevant information from both vector and knowledge graph sources
            - Break down complex queries into sub-questions for comprehensive answers
            - Cite sources and provide evidence-based responses
            - Handle follow-up questions and clarifications
            - Navigate complex topics with multi-step reasoning

            Key Features:
            - Hybrid search combining vector and knowledge graph approaches
            - Contextual conversation management with conversation_id tracking
            - Customizable generation parameters for response style and length
            - Source document citation with optional title inclusion
            - Streaming support for real-time responses
            - Branch management for exploring different conversation paths

            Common Use Cases:
            - Research assistance and literature review
            - Document analysis and summarization
            - Technical support and troubleshooting
            - Educational Q&A and tutoring
            - Knowledge base exploration

            The agent uses both vector search and knowledge graph capabilities to find and synthesize
            information, providing detailed, factual responses with proper attribution to source documents.
            """
            effective_settings = self._prepare_search_settings(
                auth_user, SearchMode.basic, None
            )

            if "model" not in rag_generation_config.__fields_set__:
                rag_generation_config.model = self.config.app.quality_llm

            try:
                response = await self.services.retrieval.agent(
                    message=message,
                    messages=None,
                    search_settings=effective_settings,
                    rag_generation_config=rag_generation_config,
                    task_prompt_override=None,
                    include_title_if_available=False,
                    max_tool_context_length=max_tool_context_length,
                    conversation_id=(
                        str(conversation_id) if conversation_id else None
                    ),
                    use_system_context=False,
                    override_tools=tools,
                    reasoning_agent=True,
                )

                if rag_generation_config.stream:

                    async def stream_generator():
                        try:
                            async for chunk in response:
                                if len(chunk) > 1024:
                                    for i in range(0, len(chunk), 1024):
                                        yield chunk[i : i + 1024]
                                else:
                                    yield chunk
                        except GeneratorExit:
                            # Clean up if needed, then return
                            return

                    return StreamingResponse(
                        stream_generator(), media_type="text/event-stream"
                    )  # type: ignore
                else:
                    return response
            except Exception as e:
                raise R2RException(str(e), 500) from e

        @self.router.post(
            "/retrieval/completion",
            dependencies=[Depends(self.rate_limit_dependency)],
            summary="Generate Message Completions",
            openapi_extra={
                "x-codeSamples": [
                    {
                        "lang": "Python",
                        "source": textwrap.dedent("""
                            from r2r import R2RClient

                            client = R2RClient()
                            # when using auth, do client.login(...)

                            response = client.completion(
                                messages=[
                                    {"role": "system", "content": "You are a helpful assistant."},
                                    {"role": "user", "content": "What is the capital of France?"},
                                    {"role": "assistant", "content": "The capital of France is Paris."},
                                    {"role": "user", "content": "What about Italy?"}
                                ],
                                generation_config={
                                    "model": "gpt-4o-mini",
                                    "temperature": 0.7,
                                    "max_tokens": 150,
                                    "stream": False
                                }
                            )
                            """),
                    },
                    {
                        "lang": "JavaScript",
                        "source": textwrap.dedent("""
                            const { r2rClient } = require("r2r-js");

                            const client = new r2rClient();

                            function main() {
                                const response = await client.completion({
                                    messages: [
                                        { role: "system", content: "You are a helpful assistant." },
                                        { role: "user", content: "What is the capital of France?" },
                                        { role: "assistant", content: "The capital of France is Paris." },
                                        { role: "user", content: "What about Italy?" }
                                    ],
                                    generationConfig: {
                                        model: "gpt-4o-mini",
                                        temperature: 0.7,
                                        maxTokens: 150,
                                        stream: false
                                    }
                                });
                            }

                            main();
                            """),
                    },
                    {
                        "lang": "Shell",
                        "source": textwrap.dedent("""
                            curl -X POST "https://api.example.com/retrieval/completion" \\
                                -H "Content-Type: application/json" \\
                                -H "Authorization: Bearer YOUR_API_KEY" \\
                                -d '{
                                "messages": [
                                    {"role": "system", "content": "You are a helpful assistant."},
                                    {"role": "user", "content": "What is the capital of France?"},
                                    {"role": "assistant", "content": "The capital of France is Paris."},
                                    {"role": "user", "content": "What about Italy?"}
                                ],
                                "generation_config": {
                                    "model": "gpt-4o-mini",
                                    "temperature": 0.7,
                                    "max_tokens": 150,
                                    "stream": false
                                }
                                }'
                            """),
                    },
                ]
            },
        )
        @self.base_endpoint
        async def completion(
            messages: list[Message] = Body(
                ...,
                description="List of messages to generate completion for",
                example=[
                    {
                        "role": "system",
                        "content": "You are a helpful assistant.",
                    },
                    {
                        "role": "user",
                        "content": "What is the capital of France?",
                    },
                    {
                        "role": "assistant",
                        "content": "The capital of France is Paris.",
                    },
                    {"role": "user", "content": "What about Italy?"},
                ],
            ),
            generation_config: GenerationConfig = Body(
                default_factory=GenerationConfig,
                description="Configuration for text generation",
                example={
                    "model": "gpt-4o-mini",
                    "temperature": 0.7,
                    "max_tokens": 150,
                    "stream": False,
                },
            ),
            auth_user=Depends(self.providers.auth.auth_wrapper()),
            response_model=WrappedCompletionResponse,
        ) -> WrappedLLMChatCompletion:
            """Generate completions for a list of messages.

            This endpoint uses the language model to generate completions for
            the provided messages. The generation process can be customized
            using the generation_config parameter.

            The messages list should contain alternating user and assistant
            messages, with an optional system message at the start. Each
            message should have a 'role' and 'content'.
            """

            return await self.services.retrieval.completion(
                messages=messages,
                generation_config=generation_config,
            )

        @self.router.post(
            "/retrieval/embedding",
            dependencies=[Depends(self.rate_limit_dependency)],
            summary="Generate Embeddings",
            openapi_extra={
                "x-codeSamples": [
                    {
                        "lang": "Python",
                        "source": textwrap.dedent("""
                            from r2r import R2RClient

                            client = R2RClient()
                            # when using auth, do client.login(...)

                            result = client.retrieval.embedding(
                                text="Who is Aristotle?",
                            )
                            """),
                    },
                    {
                        "lang": "JavaScript",
                        "source": textwrap.dedent("""
                            const { r2rClient } = require("r2r-js");

                            const client = new r2rClient();

                            function main() {
                                const response = await client.retrieval.embedding({
                                    text: "Who is Aristotle?",
                                });
                            }

                            main();
                            """),
                    },
                    {
                        "lang": "Shell",
                        "source": textwrap.dedent("""
                            curl -X POST "https://api.example.com/retrieval/embedding" \\
                                -H "Content-Type: application/json" \\
                                -H "Authorization: Bearer YOUR_API_KEY" \\
                                -d '{
                                "text": "Who is Aristotle?",
                                }'
                            """),
                    },
                ]
            },
        )
        @self.base_endpoint
        async def embedding(
            text: str = Body(
                ...,
                description="Text to generate embeddings for",
            ),
            auth_user=Depends(self.providers.auth.auth_wrapper()),
        ) -> WrappedEmbeddingResponse:
            """Generate embeddings for the provided text using the specified
            model.

            This endpoint uses the language model to generate embeddings for
            the provided text. The model parameter specifies the model to use
            for generating embeddings.
            """

            return await self.services.retrieval.embedding(
                text=text,
            )
