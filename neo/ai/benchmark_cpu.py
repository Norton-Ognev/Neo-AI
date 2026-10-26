# ai/benchmark_cpu.py
import time
from ollama import chat, chat


def run_benchmark():
    print("=== Старт бенчмарка T-lite-it-2.1 Q4_K_M на CPU ===")

    test_prompt = "Напомни завтра в 10 утра позвонить Ивану по поводу договора."

    # 1. Замер cold start / время ответа
    start_time = time.time()

    response = chat(
        model="t-tech/T-lite-it-2.1:q4_K_M",
        messages=[{"role": "user", "content": test_prompt}],
        options={"temperature": 0}
    )

    end_time = time.time()
    total_time = end_time - start_time

    # Извлекаем метрики из ответа Ollama (если они доступны в API)
    # Если Ollama не отдает точные цифры в вашей версии библиотеки,
    # мы считаем приблизительную скорость генерации по факту.
    content = response.message.content
    eval_count = getattr(response, 'eval_count', len(content.split()))  # fallback на слова
    eval_duration_ns = getattr(response, 'eval_duration', None)

    if eval_duration_ns:
        tokens_per_sec = eval_count / (eval_duration_ns / 1e9)
    else:
        tokens_per_sec = eval_count / total_time

    print("\n--- Результаты ---")
    print(f"Текст ответа LLM: {content}")
    print(f"Полное время ответа: {total_time:.2f} сек")
    print(f"Приблизительная скорость: {tokens_per_sec:.2f} токенов/сек")
    print("==================================================")


if __name__ == "__main__":
    run_benchmark()
