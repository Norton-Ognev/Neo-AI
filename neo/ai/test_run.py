# ai/test_run.py
import sys
import os
from datetime import datetime

# Добавляем корневую папку проекта в пути Python
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Теперь импортируем оркестратор как часть пакета ai
from ai.orchestrator import process

if __name__ == "__main__":
    print("=== Проверка работы AI-Оркестратора ===")

    # Фиксируем время, как будто сейчас воскресенье, 18 октября 2026 года, 11:15 утра
    mock_now = datetime.fromisoformat("2026-10-18T11:15:00+03:00")

    test_phrase = "Напомни завтра в 15:00 позвонить Данилу по проекту"
    print(f"\nВходной текст: '{test_phrase}'")

    result = process(test_phrase, now=mock_now)
    print("\n--- Результат распознавания ---")
    print(f"Action: {result.action}")
    print(f"Content: {result.content}")
    print(f"Datetime ISO: {result.datetime_iso}")
    print("================================")
