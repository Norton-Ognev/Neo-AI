# ai/run_tests.py
import os
import sys
import yaml
from datetime import datetime

# Настройка путей, чтобы пакет ai импортировался корректно
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir) if current_dir.endswith('ai') else current_dir
if project_root not in sys.path:
    sys.path.append(project_root)

from ai.orchestrator import process


def load_tests():
    yaml_path = os.path.join(project_root, "ai", "tests", "test_cases.yaml")
    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    print("=== Запуск автоматического тестирования тест-сета ===")
    tests = load_tests()

    # Фиксируем виртуальное время для тестов (воскресенье, 18 октября 2026)
    mock_now = datetime.fromisoformat("2026-10-18T11:15:00+03:00")

    stats = {
        "create_task": {"correct": 0, "total": 0},
        "create_meeting": {"correct": 0, "total": 0},
        "list_events": {"correct": 0, "total": 0},
        "search_events": {"correct": 0, "total": 0},
        "clarify": {"correct": 0, "total": 0}
    }

    errors = []

    for i, case in enumerate(tests, 1):
        user_text = case["user_text"]
        expected_action = case["expected"]["action"]

        print(f"[{i}/{len(tests)}] Обработка: '{user_text}'...")

        # Прогон через ИИ-оркестратор
        try:
            result = process(user_text, now=mock_now)
            actual_action = result.action
        except Exception as e:
            actual_action = f"ERROR: {str(e)}"

        # Считаем статистику
        if expected_action in stats:
            stats[expected_action]["total"] += 1
            if actual_action == expected_action:
                stats[expected_action]["correct"] += 1
            else:
                errors.append(f"Текст: '{user_text}' | Ожидалось: {expected_action} | Получено: {actual_action}")

    # Вывод результатов в консоль
    print("\n=== Результаты тестирования ===")
    total_correct = 0
    total_cases = 0

    for intent, data in stats.items():
        correct = data["correct"]
        total = data["total"]
        total_correct += correct
        total_cases += total
        accuracy = (correct / total * 100) if total > 0 else 0
        print(f"Интент '{intent}': {correct}/{total} правильно ({accuracy:.1f}%)")

    final_accuracy = (total_correct / total_cases * 100) if total_cases > 0 else 0
    print(f"\nОбщая точность (Accuracy): {total_correct}/{total_cases} ({final_accuracy:.1f}%)")

    if errors:
        print("\n--- Примеры ошибок ---")
        for err in errors[:5]:  # Выведем первые 5 ошибок
            print(f"[КРИВОЙ ИНТЕНТ] {err}")

    # Автоматическое обновление docs/benchmark.md
    update_benchmark_file(stats, total_correct, total_cases, errors)


def update_benchmark_file(stats, total_correct, total_cases, errors):
    benchmark_path = os.path.join(project_root, "docs", "benchmark.md")
    if not os.path.exists(benchmark_path):
        return

    with open(benchmark_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Формируем новую таблицу точности
    table_lines = [
        "## Accuracy на тест-сете\n",
        "| Интент | Правильно / Всего |",
        "|---|---|"
    ]
    for intent, data in stats.items():
        table_lines.append(f"| {intent} | {data['correct']} / {data['total']} |")
    table_lines.append(f"| **Итого** | **{total_correct} / {total_cases}** |\n")

    # Формируем блок ошибок
    error_lines = ["## Примеры ошибок"]
    if errors:
        for err in errors:
            error_lines.append(f"* {err}")
    else:
        error_lines.append("* Ошибок не обнаружено! Идеальное совпадение.")

    # Заменяем старые блоки через регулярки или простым сплитом
    try:
        parts = content.split("## Accuracy на тест-сете")
        header_part = parts[0]

        new_content = header_part + "\n".join(table_lines) + "\n" + "\n".join(error_lines) + "\n"
        with open(benchmark_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        print("\n[INFO] Файл docs/benchmark.md успешно обновлен новыми метриками точности!")
    except Exception as e:
        print(f"[WARNING] Не удалось автоматически обновить benchmark.md: {e}")


if __name__ == "__main__":
    main()
