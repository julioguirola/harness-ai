import json
import os
from collections.abc import Iterator

import requests
from openai import OpenAI
from pydantic import BaseModel


class ReasoningOption(BaseModel):
    type: str
    values: list[str] | None = None


class Limit(BaseModel):
    context: int
    output: int


class AIModel(BaseModel):
    id: str
    name: str
    reasoning: bool
    reasoning_options: list[ReasoningOption] | None = None
    tool_call: bool
    limit: Limit
    temperature: bool


class Provider(BaseModel):
    models: dict[str, AIModel]


MODELS: dict[str, AIModel] = {}


def fetch_provider(provider: str):
    global MODELS
    with open("models.json", "r", encoding="utf-8") as file:
        try:
            cached = Provider.model_validate_json(file.read())
            if len(cached.models) > 0:
                MODELS = cached.models
                return
        except:
            pass

    res = requests.get("https://models.dev/api.json")
    with open("models.json", "w", encoding="utf-8") as file:
        file.write(json.dumps(res.json()[provider], indent=4))
    provider_fetched = Provider.model_validate(res.json()[provider])
    MODELS = provider_fetched.models


fetch_provider("nvidia")

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ["NVIDIA_API_KEY"],
)

MODEL: AIModel = MODELS[next(iter(MODELS))]


COMANDS = {
    "/model": "cambiar el modelo de la conversación",
    "/exit": "salir de la aplicación",
}


def select_model(model: AIModel):
    global MODEL
    MODEL = model


conversation: list[dict[str, str]] = []


def stream_chat(user_content: str) -> Iterator[tuple[str, str]]:
    print(f"Llamando al modelo {MODEL.id} con context={MODEL.limit.context}")
    conversation.append({"role": "user", "content": user_content})

    completion = client.chat.completions.create(
        model=MODEL.id,
        messages=conversation,
        temperature=0.2,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
        # reasoning_effort=
        stream=True,
        max_completion_tokens=MODEL.limit.context,
    )

    answer = ""
    for chunk in completion:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            yield "reasoning", reasoning

        if delta.content:
            answer += delta.content
            yield "content", delta.content

    conversation.append({"role": "assistant", "content": answer})
