import os

from openai import OpenAI

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ["NVIDIA_API_KEY"],
)

MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"

# Estado de la conversación
conversation = []


def chat(user_content):
    """Agrega el mensaje del usuario, llama al modelo, guarda la respuesta."""
    conversation.append({"role": "user", "content": user_content})

    completion = client.chat.completions.create(
        model=MODEL,
        messages=conversation,
        temperature=1,
        top_p=0.95,
        max_tokens=16384,
        seed=42,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
        stream=True,
    )

    content_started = False

    answer = ""
    for chunk in completion:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            print(reasoning, end="", flush=True)

        if delta.content:
            if not content_started:
                print("\n")
                content_started = not content_started
            print(delta.content, end="", flush=True)
            answer += delta.content

    print()
    conversation.append({"role": "assistant", "content": answer})
    return answer


def main():

    while True:
        text = input("\n> ").strip()
        if text.lower() in {"/q"}:
            break
        chat(text)


if __name__ == "__main__":
    main()
