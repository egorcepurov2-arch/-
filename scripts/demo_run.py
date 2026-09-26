#!/usr/bin/env python3
"""
Интерактивный демонстрационный запуск системы диспетчеризации (VUPSEN Squad).
Наглядно визуализирует обработку пакетов телеметрии, состояние дорожного коридора,
здоровье инфраструктуры, распределение ресурсов (пульт поддержки, safe stops)
и решения по каждому беспилотному автомобилю.
"""

import argparse
from collections import Counter
import gzip
import io
import json
from pathlib import Path
import sys
import time
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_reference_data
from src.core.state import SystemState


# Цветовая палитра ANSI для форматирования в терминале
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def print_step_dashboard(step: int, packet_id: str, elapsed_ms: float, events_count: int, decision: dict, state: SystemState):
    print(f"\n{BOLD}{CYAN}╔═══════════════════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║  ШАГ {step:03d} | ПАКЕТ: {packet_id:<18} | ВРЕМЯ РАСЧЁТА: {elapsed_ms:6.2f} мс | СОБЫТИЙ: {events_count:<5} ║{RESET}")
    print(f"{BOLD}{CYAN}╚═══════════════════════════════════════════════════════════════════════════════╝{RESET}")

    # 1. Дорожные сегменты
    seg_states = Counter(s["state"] for s in decision["state_estimates"])
    closed_segs = [s["segment_id"] for s in decision["state_estimates"] if s["state"] == "CLOSED"]
    closed_str = f"{RED}{BOLD}{', '.join(closed_segs)}{RESET}" if closed_segs else f"{GREEN}нет перекрытий{RESET}"

    print(f"{BOLD}🛣️  Дорожная обстановка (86 сегментов):{RESET}")
    print(
        f"   • {GREEN}OPEN:{RESET} {seg_states.get('OPEN', 0):<3} | "
        f"   • {YELLOW}CONGESTED:{RESET} {seg_states.get('CONGESTED', 0):<3} | "
        f"   • {YELLOW}PARTIAL_BLOCK:{RESET} {seg_states.get('PARTIAL_BLOCK', 0):<3} | "
        f"   • {RED}CLOSED:{RESET} {seg_states.get('CLOSED', 0):<3}"
    )
    print(f"   • Закрытые участки: {closed_str}")

    # 2. Инфраструктурные датчики
    src_statuses = Counter(s["status"] for s in decision["source_assessments"])
    failed_sources = [
        f"{s['source_id']} ({','.join(s['fault_types'])})"
        for s in decision["source_assessments"] if s["status"] != "OK"
    ]
    fault_str = f"{YELLOW}{'; '.join(failed_sources[:4])}{RESET}" if failed_sources else f"{GREEN}все 50 источников исправны{RESET}"

    print(f"\n{BOLD}📡 Состояние инфраструктуры (50 источников):{RESET}")
    print(
        f"   • {GREEN}OK:{RESET} {src_statuses.get('OK', 0):<3} | "
        f"   • {YELLOW}DEGRADED:{RESET} {src_statuses.get('DEGRADED', 0):<3} | "
        f"   • {RED}FAILED:{RESET} {src_statuses.get('FAILED', 0):<3}"
    )
    print(f"   • Обнаруженные сбои: {fault_str}")

    # 3. Беспилотные транспортные средства (ВАТС)
    odd_statuses = Counter(v["odd_status"] for v in decision["vehicle_assessments"])
    violated_vids = [v["vehicle_id"] for v in decision["vehicle_assessments"] if v["odd_status"] == "VIOLATED"]
    print(f"\n{BOLD}🚚 Парк ВАТС (72 автомобиля) & ODD:{RESET}")
    print(
        f"   • {GREEN}COMPLIANT:{RESET} {odd_statuses.get('COMPLIANT', 0):<3} | "
        f"   • {RED}VIOLATED:{RESET} {odd_statuses.get('VIOLATED', 0):<3} | "
        f"   • {DIM}INACTIVE/UNKNOWN:{RESET} {odd_statuses.get('UNKNOWN', 0):<3}"
    )
    if violated_vids:
        print(f"   • Вне ODD ({len(violated_vids)}): {RED}{', '.join(violated_vids[:8])}{'...' if len(violated_vids) > 8 else ''}{RESET}")

    # 4. Управляющие решения диспетчерского центра
    actions = Counter(a["motion_action"] for a in decision["vehicle_actions"])
    remote_vids = [a["vehicle_id"] for a in decision["vehicle_actions"] if a["remote_support_required"]]
    safe_stops_used = [
        f"{a['vehicle_id']} -> {a['safe_stop_id']}"
        for a in decision["vehicle_actions"] if a.get("safe_stop_id")
    ]
    rerouted_vids = [a["vehicle_id"] for a in decision["vehicle_actions"] if a["motion_action"] == "REROUTE"]

    print(f"\n{BOLD}🎮 Принятые решения ДЦ:{RESET}")
    print(
        f"   • {GREEN}CONTINUE:{RESET} {actions.get('CONTINUE', 0):<3} | "
        f"   • {YELLOW}LIMIT_SPEED:{RESET} {actions.get('LIMIT_SPEED', 0):<3} | "
        f"   • {CYAN}REROUTE:{RESET} {actions.get('REROUTE', 0):<3} | "
        f"   • {RED}SAFE_STOP:{RESET} {actions.get('SAFE_STOP', 0):<3} | "
        f"   • {MAGENTA}HOLD:{RESET} {actions.get('HOLD', 0):<3}"
    )
    if rerouted_vids:
        print(f"   • Объезд (REROUTE): {CYAN}{', '.join(rerouted_vids[:6])}{RESET}")
    if safe_stops_used:
        print(f"   • Остановка (SAFE_STOP): {RED}{', '.join(safe_stops_used[:6])}{RESET}")

    # 5. Ресурсы
    rem_color = RED if len(remote_vids) >= 6 else (YELLOW if len(remote_vids) >= 4 else GREEN)
    print(f"\n{BOLD}⚡ Ресурсы коридора:{RESET}")
    print(f"   • Дистанционная поддержка: {rem_color}{len(remote_vids)} / 6 сессий{RESET} {f'(ВАТС: {round_brackets(remote_vids)})' if remote_vids else ''}")
    
    # Safe stops occupancy
    occ_map = state.resource_manager.safe_stops.get_occupancy_map()
    occupied_stops = [
        f"{sid}: {cnt}/{state.resource_manager.safe_stops.get_capacity(sid)}"
        for sid, cnt in occ_map.items() if cnt > 0
    ]
    occ_str = ", ".join(occupied_stops) if occupied_stops else "все 10 площадок свободны"
    print(f"   • Площадки безопасной остановки: {occ_str}")


def round_brackets(vids):
    return ", ".join(vids[:6])


def load_packets(scenario: str, limit: int, start_step: int, file_path: str = None) -> list:
    """Load packets either from file or from data archive."""
    if file_path:
        p = Path(file_path)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        packets = []
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s:
                    pkt = json.loads(s)
                    step = pkt.get("step", 0)
                    if step >= start_step:
                        packets.append(pkt)
                        if len(packets) >= limit:
                            break
        return packets

    # Load from zip
    zip_path = Path("/home/vip/конкурс/Беспилотный_коридор.zip")
    if not zip_path.exists():
        # Fallback to fixture
        return load_packets(scenario, limit, start_step, "tests/fixtures/sample_packets.ndjson")

    packets = []
    with zipfile.ZipFile(zip_path) as outer:
        with outer.open("03_Данные_Беспилотный_коридор.zip") as inner_file:
            inner_bytes = io.BytesIO(inner_file.read())
            with zipfile.ZipFile(inner_bytes) as inner:
                inner_path = f"02_train/{scenario}/packets.ndjson.gz"
                with inner.open(inner_path) as p_file:
                    with gzip.GzipFile(fileobj=p_file) as gz:
                        for line in gz:
                            line_str = line.decode("utf-8").strip()
                            if line_str:
                                pkt = json.loads(line_str)
                                step = pkt.get("step", 0)
                                if step >= start_step:
                                    packets.append(pkt)
                                    if len(packets) >= limit:
                                        break
    return packets


def main():
    parser = argparse.ArgumentParser(description="Демонстрация работы алгоритма диспетчеризации (VUPSEN Squad)")
    parser.add_argument(
        "--file",
        "-f",
        default=None,
        help="Путь к файлу NDJSON с пакетами (по умолчанию: fixtures/sample_packets.ndjson)",
    )
    parser.add_argument(
        "--scenario",
        "-s",
        default="TRAIN-001",
        choices=["TRAIN-001", "TRAIN-002", "TRAIN-003", "TRAIN-004"],
        help="Обучающий сценарий из архива (TRAIN-001..TRAIN-004)",
    )
    parser.add_argument(
        "--start-step",
        type=int,
        default=1,
        help="Начальный шаг симуляции",
    )
    parser.add_argument(
        "--limit",
        "-n",
        type=int,
        default=5,
        help="Количество пакетов для демонстрации",
    )
    parser.add_argument(
        "--delay",
        "-d",
        type=float,
        default=0.4,
        help="Пауза между шагами в секундах (для удобства визуального чтения)",
    )
    args = parser.parse_args()

    default_file = None if args.file else ("tests/fixtures/sample_packets.ndjson" if args.scenario == "TRAIN-001" and args.start_step == 1 else None)
    target_file = args.file or default_file

    print(f"{BOLD}{GREEN}Загрузка справочных каталогов (86 сегментов, 50 источников, 72 ВАТС)...{RESET}")
    ref = load_reference_data()
    state = SystemState(ref=ref)

    source_desc = target_file if target_file else f"Архив: сценарий {args.scenario} (с шага {args.start_step})"
    print(f"{BOLD}Источник пакетов: {CYAN}{source_desc}{RESET}")
    packets = load_packets(scenario=args.scenario, limit=args.limit, start_step=args.start_step, file_path=target_file)
    print(f"{BOLD}Загружено пакетов для прогона: {len(packets)}{RESET}")

    total_time_ms = 0.0

    for i, pkt in enumerate(packets, start=1):
        step_id = pkt.get("step", i)
        packet_id = pkt.get("packet_id", f"PKT-{i:04d}")
        events = pkt.get("events", [])

        t0 = time.perf_counter()
        decision = state.process_packet(pkt)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        total_time_ms += elapsed_ms

        print_step_dashboard(
            step=step_id,
            packet_id=packet_id,
            elapsed_ms=elapsed_ms,
            events_count=len(events),
            decision=decision,
            state=state,
        )

        if args.delay > 0 and i < len(packets):
            time.sleep(args.delay)

    avg_ms = total_time_ms / len(packets) if packets else 0.0
    print(f"\n{BOLD}{GREEN}✔ Демонстрация успешно завершена!{RESET}")
    print(f"  • Обработано пакетов: {len(packets)}")
    print(f"  • Среднее время отклика: {BOLD}{avg_ms:.2f} мс{RESET} (Лимит регламента: 2000 мс)")
    print(f"  • Запас производительности: {BOLD}{2000.0 / max(0.01, avg_ms):.1f}x{RESET} быстрее требований регламента!\n")


if __name__ == "__main__":
    main()
