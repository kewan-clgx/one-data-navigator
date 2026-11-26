from google.adk.models.llm_request import LlmRequest
from google.genai import types

from odn.agent import copilot_llm


def _request() -> LlmRequest:
    return LlmRequest(
        contents=[
            types.Content(role="user", parts=[types.Part(text="Top 3 states by businesses?")]),
            types.Content(
                role="model",
                parts=[
                    types.Part(text="thinking…", thought=True),
                    types.Part(function_call=types.FunctionCall(name="run_sql", args={"sql": "SELECT 1"})),
                ],
            ),
            types.Content(
                role="user",
                parts=[types.Part(function_response=types.FunctionResponse(name="run_sql", response={"rows": [[1]]}))],
            ),
        ],
        config=types.GenerateContentConfig(
            system_instruction="You are EasyQuery.",
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name="run_sql",
                            description="Run read-only SQL.",
                            parameters=types.Schema(
                                type=types.Type.OBJECT,
                                properties={
                                    "sql": types.Schema(type=types.Type.STRING, description="the query"),
                                    "limit": types.Schema(type=types.Type.INTEGER, nullable=True),
                                    "tables": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
                                },
                                required=["sql"],
                            ),
                        )
                    ]
                )
            ],
        ),
    )


def test_adk_tool_schema_becomes_json_schema() -> None:
    [fd] = copilot_llm.function_declarations(_request())
    assert copilot_llm.declaration_schema(fd) == {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "the query"},
            "limit": {"type": ["integer", "null"]},
            "tables": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["sql"],
    }


def test_transcript_carries_tool_calls_and_results_but_not_thoughts() -> None:
    req = _request()
    text = copilot_llm.transcript(list(req.contents))
    assert text.split("\n\n") == [
        "[User]\nTop 3 states by businesses?",
        '[Assistant called tool run_sql] {"sql": "SELECT 1"}',
        '[Tool result: run_sql]\n{"rows": [[1]]}',
    ]
    assert copilot_llm.system_instruction(req) == "You are EasyQuery."


def test_long_tool_results_are_clipped() -> None:
    clipped = copilot_llm._clip("x" * 50, limit=10)
    assert clipped.startswith("x" * 10) and "40 more characters omitted" in clipped


def test_tool_calls_are_handed_back_to_adk_as_function_calls() -> None:
    resp = copilot_llm.response_from([("run_sql", {"sql": "SELECT 2"})], None, "claude-x", {"input": 7, "output": 3})
    [part] = resp.content.parts
    assert (part.function_call.name, part.function_call.args) == ("run_sql", {"sql": "SELECT 2"})
    assert part.function_call.id.startswith("copilot-")
    assert (resp.usage_metadata.prompt_token_count, resp.usage_metadata.candidates_token_count) == (7, 3)
    assert resp.model_version == "claude-x"

    answer = copilot_llm.response_from([], "CA leads.", "claude-x", {})
    assert [p.text for p in answer.content.parts] == ["CA leads."]
