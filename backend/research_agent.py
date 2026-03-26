from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from .model_client import ProviderConfig, make_model_invoker
from .search_client import dedupe_source_urls, search_web

ProgressFn = Callable[[dict[str, Any]], Awaitable[None]]


class ResearchState(TypedDict, total=False):
    topic: str
    context: str | None
    depth: str
    mode: str
    provider_config: dict[str, Any]
    search_config: dict[str, Any]
    user_sources: list[str]
    deduped_user_sources: list[str]
    plan: dict[str, Any]
    web_evidence: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    citations: list[str]
    report: str


def extract_json_block(text: str) -> dict[str, Any] | None:
    if not text:
        return None

    stripped = text.strip()
    if stripped.startswith("```"):
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except Exception:
                pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            return None
    return None


def sanitize_plan(plan: dict[str, Any] | None, topic: str) -> dict[str, Any]:
    if not isinstance(plan, dict):
        return {
            "topic": topic,
            "objective": f"Deliver a high-confidence deep research brief about {topic}",
            "subQuestions": [
                f"What is the current state of {topic}?",
                f"What are the key risks and opportunities in {topic}?",
                f"What actionable next steps should be prioritized for {topic}?",
            ],
            "outputSections": [
                "Executive Summary",
                "Core Findings",
                "Evidence & Source Links",
                "Counterarguments",
                "Risks & Opportunities",
                "30-Day Watchlist",
                "Prioritized Action Plan",
            ],
        }

    return {
        "topic": plan.get("topic") or topic,
        "objective": plan.get("objective") or f"Research {topic}",
        "subQuestions": (plan.get("subQuestions") or [f"What matters most about {topic} right now?"])[0:8],
        "outputSections": (plan.get("outputSections") or ["Executive Summary", "Key Findings", "Action Plan"])[0:10],
    }


def format_search_evidence(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No external search evidence available."

    chunks: list[str] = []
    for idx, item in enumerate(items, start=1):
        chunks.append(
            f"{idx}. {item.get('title')}\n"
            f"URL: {item.get('normalizedUrl') or item.get('url')}\n"
            f"Snippet: {item.get('snippet') or 'N/A'}"
        )
    return "\n\n".join(chunks)


def to_citation_lines(urls: list[str]) -> str:
    if not urls:
        return "- No citations available."
    return "\n".join(f"- [{idx}] {url}" for idx, url in enumerate(urls, start=1))


def depth_to_question_count(depth: str) -> int:
    if depth == "fast":
        return 3
    if depth == "deep":
        return 7
    return 5


def normalize_search_queries(obj: dict[str, Any] | None, topic: str, sub_questions: list[str]) -> list[str]:
    queries = obj.get("queries") if isinstance(obj, dict) else None
    if isinstance(queries, list) and queries:
        return [str(x).strip() for x in queries if str(x).strip()][:6]

    return [topic] + [f"{topic} {q}" for q in sub_questions[: min(3, len(sub_questions))]]


def merge_citations(user_sources: list[str], web_evidence: list[dict[str, Any]]) -> list[str]:
    urls = list(user_sources)
    urls.extend([x.get("normalizedUrl") or x.get("url") for x in web_evidence])
    return dedupe_source_urls([x for x in urls if x])


async def call_agent(
    *,
    invoke,
    role_name: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
) -> dict[str, str]:
    text = await invoke(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
    )
    return {"roleName": role_name, "text": text}


def build_research_graph(*, invoke, emit):
    async def planner_node(state: ResearchState) -> ResearchState:
        topic = state["topic"]
        context = state.get("context")
        depth = state["depth"]

        await emit({"stage": "planning", "message": "Planner drafting research plan", "progress": 12, "payload": {"agent": "planner"}})

        planner = await call_agent(
            invoke=invoke,
            role_name="planner",
            temperature=0.1,
            system_prompt="You are Planner Agent. Return ONLY JSON with keys: topic, objective, subQuestions (array), outputSections (array).",
            user_prompt="\n".join(
                [
                    f"Topic: {topic}",
                    f"Context: {context or 'N/A'}",
                    f"Depth: {depth}",
                    f"TargetQuestionCount: {depth_to_question_count(depth)}",
                    "Focus on decision-grade sub-questions.",
                ]
            ),
        )

        plan = sanitize_plan(extract_json_block(planner["text"]), topic)
        plan["subQuestions"] = plan["subQuestions"][: depth_to_question_count(depth)]

        deduped_user_sources = dedupe_source_urls(state.get("user_sources") or [])

        await emit(
            {
                "stage": "planning",
                "message": f"Planner completed ({len(plan['subQuestions'])} sub-questions)",
                "progress": 20,
                "payload": {"agent": "planner", "subQuestions": plan["subQuestions"]},
            }
        )

        return {
            "plan": plan,
            "deduped_user_sources": deduped_user_sources,
            "web_evidence": [],
            "findings": [],
            "citations": [],
            "report": "",
        }

    async def searcher_node(state: ResearchState) -> ResearchState:
        topic = state["topic"]
        plan = state["plan"]
        search_config = state["search_config"]

        if search_config["provider"] == "none":
            await emit({"stage": "search", "message": "Searcher skipped web search (provider=none)", "progress": 42, "payload": {"agent": "searcher"}})
            return {"web_evidence": []}

        await emit(
            {
                "stage": "search",
                "message": f"Searcher preparing queries ({search_config['provider']})",
                "progress": 28,
                "payload": {"agent": "searcher"},
            }
        )

        searcher = await call_agent(
            invoke=invoke,
            role_name="searcher",
            temperature=0.1,
            system_prompt='You are Searcher Agent. Return ONLY JSON: {"queries": string[]}.',
            user_prompt="\n\n".join(
                [
                    f"Main Topic: {topic}",
                    "Sub-questions:\n" + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(plan["subQuestions"])),
                    "Generate high-signal web search queries for authoritative sources.",
                ]
            ),
        )

        search_queries = normalize_search_queries(extract_json_block(searcher["text"]), topic, plan["subQuestions"])

        gathered: list[dict[str, Any]] = []
        for query in search_queries:
            items = await search_web(
                provider=search_config["provider"],
                query=query,
                max_results=search_config["maxResults"],
                tavily_api_key=search_config.get("tavilyApiKey"),
                serp_api_key=search_config.get("serpApiKey"),
            )
            gathered.extend(items)
            await emit(
                {
                    "stage": "search",
                    "message": f"Searcher fetched evidence for query: {query}",
                    "progress": 35,
                    "payload": {"agent": "searcher", "query": query, "fetched": len(items)},
                }
            )

        deduped: dict[str, dict[str, Any]] = {}
        for item in gathered:
            key = item.get("normalizedUrl") or item.get("url")
            if not key or key in deduped:
                continue
            deduped[key] = item

        web_evidence = list(deduped.values())[: search_config["maxResults"] * 2]

        await emit(
            {
                "stage": "search",
                "message": f"Searcher finalized evidence ({len(web_evidence)} unique sources)",
                "progress": 42,
                "payload": {"agent": "searcher", "citations": [x.get("normalizedUrl") or x.get("url") for x in web_evidence]},
            }
        )

        return {"web_evidence": web_evidence}

    async def analyst_node(state: ResearchState) -> ResearchState:
        topic = state["topic"]
        context = state.get("context")
        mode = state["mode"]
        plan = state["plan"]
        deduped_user_sources = state.get("deduped_user_sources") or []
        web_evidence = state.get("web_evidence") or []

        findings: list[dict[str, Any]] = []
        total = len(plan["subQuestions"])

        for index, question in enumerate(plan["subQuestions"]):
            phase_progress = 45 + round(((index + 1) / max(total, 1)) * 35)

            await emit(
                {
                    "stage": "analysis",
                    "message": f"Analyst processing question {index + 1}/{total}",
                    "progress": phase_progress,
                    "payload": {"agent": "analyst", "question": question},
                }
            )

            analyst = await call_agent(
                invoke=invoke,
                role_name="analyst",
                temperature=0.2,
                system_prompt="You are Analyst Agent. Output markdown with sections: Facts, Inferences, Recommendations, Confidence(0-100). Use [n] citation markers when possible.",
                user_prompt="\n\n".join(
                    [
                        f"Topic: {topic}",
                        f"Question: {question}",
                        f"Context: {context or 'N/A'}",
                        (
                            "User-provided sources:\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(deduped_user_sources))
                            if deduped_user_sources
                            else "User-provided sources: none"
                        ),
                        "Web evidence:\n" + format_search_evidence(web_evidence),
                    ]
                ),
            )

            if mode == "multi":
                await emit(
                    {
                        "stage": "analysis",
                        "message": f"Critic reviewing question {index + 1}/{total}",
                        "progress": min(85, phase_progress + 2),
                        "payload": {"agent": "critic", "question": question},
                    }
                )

                citation_urls_for_critic = merge_citations(deduped_user_sources, web_evidence)

                critic = await call_agent(
                    invoke=invoke,
                    role_name="critic",
                    temperature=0.1,
                    system_prompt="You are Critic Agent. Improve analytical rigor. Return markdown with sections exactly: Fact Check, Gaps, Revised Answer, Confidence(0-100).",
                    user_prompt="\n\n".join(
                        [
                            f"Topic: {topic}",
                            f"Question: {question}",
                            "Analyst draft:",
                            analyst["text"],
                            "Available citations:\n" + to_citation_lines(citation_urls_for_critic),
                        ]
                    ),
                )

                findings.append(
                    {
                        "question": question,
                        "answer": critic["text"],
                        "agentTrace": [
                            {"role": "analyst", "text": analyst["text"]},
                            {"role": "critic", "text": critic["text"]},
                        ],
                    }
                )
            else:
                findings.append(
                    {
                        "question": question,
                        "answer": analyst["text"],
                        "agentTrace": [{"role": "analyst", "text": analyst["text"]}],
                    }
                )

        return {"findings": findings}

    async def synthesizer_node(state: ResearchState) -> ResearchState:
        topic = state["topic"]
        plan = state["plan"]
        findings = state.get("findings") or []
        deduped_user_sources = state.get("deduped_user_sources") or []
        web_evidence = state.get("web_evidence") or []

        await emit({"stage": "synthesis", "message": "Synthesizer generating final report", "progress": 90, "payload": {"agent": "synthesizer"}})

        citations = merge_citations(deduped_user_sources, web_evidence)

        synthesizer = await call_agent(
            invoke=invoke,
            role_name="synthesizer",
            temperature=0.2,
            system_prompt=(
                "You are Synthesizer Agent. Produce a final markdown report with these sections exactly: "
                "Executive Summary, Core Findings, Evidence & Source Links, Counterarguments, "
                "Risks & Opportunities, 30-Day Watchlist, Prioritized Action Plan. Distinguish facts, inferences, and recommendations."
            ),
            user_prompt="\n".join(
                [
                    f"Topic: {topic}",
                    f"Objective: {plan['objective']}",
                    f"Output sections requested: {', '.join(plan['outputSections'])}",
                    "Agent-reviewed findings:",
                    *[f"\n[Q{i + 1}] {item['question']}\n{item['answer']}" for i, item in enumerate(findings)],
                    f"\nAvailable citations:\n{to_citation_lines(citations)}",
                ]
            ),
        )

        return {
            "citations": citations,
            "report": synthesizer["text"],
        }

    graph = StateGraph(ResearchState)
    graph.add_node("planner", planner_node)
    graph.add_node("searcher", searcher_node)
    graph.add_node("analyst", analyst_node)
    graph.add_node("synthesizer", synthesizer_node)

    graph.add_edge(START, "planner")
    graph.add_edge("planner", "searcher")
    graph.add_edge("searcher", "analyst")
    graph.add_edge("analyst", "synthesizer")
    graph.add_edge("synthesizer", END)

    return graph.compile()


async def run_deep_research(
    *,
    topic: str,
    context: str | None,
    user_sources: list[str],
    depth: str,
    provider_config: dict[str, Any],
    search_config: dict[str, Any],
    agent_mode: str,
    on_progress: ProgressFn,
) -> dict[str, Any]:
    mode = "single" if agent_mode == "single" else "multi"
    invoke = make_model_invoker(ProviderConfig(**provider_config))

    async def emit(event: dict[str, Any]) -> None:
        await on_progress(event)

    await emit(
        {
            "stage": "init",
            "message": f"Starting {mode}-agent research workflow",
            "progress": 5,
            "payload": {"agentMode": mode},
        }
    )

    graph = build_research_graph(invoke=invoke, emit=emit)
    final_state = await graph.ainvoke(
        {
            "topic": topic,
            "context": context,
            "depth": depth,
            "mode": mode,
            "provider_config": provider_config,
            "search_config": search_config,
            "user_sources": user_sources,
        }
    )

    await emit({"stage": "complete", "message": "Research completed", "progress": 100, "payload": {"agentMode": mode}})

    return {
        "plan": final_state.get("plan"),
        "findings": final_state.get("findings") or [],
        "citations": final_state.get("citations") or [],
        "webEvidence": final_state.get("web_evidence") or [],
        "report": final_state.get("report") or "",
        "meta": {
            "provider": provider_config["provider"],
            "model": provider_config["openai"]["model"] if provider_config["provider"] == "openai" else provider_config["local"]["model"],
            "searchProvider": search_config["provider"],
            "agentMode": mode,
            "orchestrator": "langgraph",
            "depth": depth,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
        },
    }
