import os
from collections.abc import Iterator

from openai import OpenAI

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ["NVIDIA_API_KEY"],
)

MODELS = [
    "deepseek-ai/deepseek-v4.1-flash",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "z-ai/glm-5.3-flash",
]

MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"


COMANDS = {
    "/model": "cambiar el modelo de la conversación",
    "/exit": "salir de la aplicación",
}


def select_model(model: str):
    global MODEL
    MODEL = model

conversation: list[dict[str, str]] = []


def stream_chat(user_content: str) -> Iterator[tuple[str, str]]:
    conversation.append({"role": "user", "content": user_content})

    completion = client.chat.completions.create(
        model=MODEL,
        messages=conversation,
        temperature=1,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
        stream=True,
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
