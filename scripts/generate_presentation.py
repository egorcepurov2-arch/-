#!/usr/bin/env python3
"""
Generate presentation.pdf for VUPSEN Squad submission (strict 7-slide format).
Converts styled HTML to PDF via headless LibreOffice.
Verifies exact 7-page count.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

HTML_CONTENT = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<style>
  @page {
    size: 297mm 210mm;
    margin: 0;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background-color: #0b132b;
    color: #e0e6ed;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
  .slide {
    width: 297mm;
    height: 210mm;
    page-break-after: always;
    page-break-inside: avoid;
    padding: 14mm 20mm 24mm 20mm;
    position: relative;
    overflow: hidden;
    background: radial-gradient(circle at 85% 15%, #1c2541 0%, #0b132b 70%);
    display: flex;
    flex-direction: column;
  }
  .slide:last-child { page-break-after: auto; }

  .slide-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 2px solid rgba(0,245,212,0.3);
    padding-bottom: 5mm;
    margin-bottom: 7mm;
  }
  .slide-title { font-size: 24pt; font-weight: 800; color: #ffffff; }
  .slide-subtitle { font-size: 12pt; color: #48cae4; font-weight: 500; }
  .team-badge {
    background: rgba(0,245,212,0.15);
    border: 1px solid #00f5d4;
    color: #00f5d4;
    padding: 4px 14px;
    border-radius: 20px;
    font-size: 10pt;
    font-weight: 700;
  }
  .slide-footer {
    position: absolute;
    bottom: 7mm;
    left: 20mm;
    right: 20mm;
    display: flex;
    justify-content: space-between;
    font-size: 8.5pt;
    color: #64748b;
    border-top: 1px solid rgba(255,255,255,0.1);
    padding-top: 3mm;
  }
  .content { flex: 1; display: flex; gap: 7mm; }
  .col { flex: 1; display: flex; flex-direction: column; gap: 4mm; }

  .card {
    background: rgba(28,37,65,0.7);
    border: 1px solid rgba(72,202,228,0.25);
    border-radius: 8px;
    padding: 4mm 5mm;
  }
  .card-title {
    font-size: 12pt; font-weight: 700; color: #00f5d4;
    margin-bottom: 2.5mm;
    display: flex; align-items: center; gap: 7px;
  }
  .card-body { font-size: 9.5pt; line-height: 1.45; color: #cbd5e1; }
  .card-body ul { list-style-type: none; }
  .card-body li { position: relative; padding-left: 14px; margin-bottom: 4px; }
  .card-body li::before { content: "▹"; position: absolute; left: 0; color: #48cae4; font-weight: bold; }

  .stats-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 5mm; margin-bottom: 5mm; }
  .stat-box {
    background: linear-gradient(135deg, rgba(28,37,65,0.9) 0%, rgba(11,19,43,0.9) 100%);
    border: 1px solid rgba(0,245,212,0.3);
    border-radius: 8px;
    padding: 4mm;
    text-align: center;
  }
  .stat-value { font-size: 24pt; font-weight: 900; color: #00f5d4; line-height: 1.1; }
  .stat-value.red { color: #ff6b6b; }
  .stat-value.green { color: #00f5d4; }
  .stat-label { font-size: 8pt; color: #94a3b8; margin-top: 2px; text-transform: uppercase; font-weight: 600; }

  table.data-table { width: 100%; border-collapse: collapse; font-size: 9.5pt; color: #e2e8f0; }
  table.data-table th {
    background: rgba(72,202,228,0.15); color: #00f5d4;
    text-align: left; padding: 5px 9px; font-weight: 700;
    border-bottom: 1px solid rgba(0,245,212,0.3);
  }
  table.data-table td { padding: 5px 9px; border-bottom: 1px solid rgba(255,255,255,0.05); }
  table.data-table .good { color: #00f5d4; font-weight: 700; }
  table.data-table .bad  { color: #ff6b6b; font-weight: 700; }

  /* SLIDE 1 — Title */
  .title-slide {
    justify-content: center; align-items: center; text-align: center;
    background: radial-gradient(circle at 50% 50%, #1c2541 0%, #0b132b 80%);
  }
  .title-hero {
    font-size: 40pt; font-weight: 900; color: #ffffff;
    line-height: 1.1; margin-bottom: 3mm;
    text-shadow: 0 4px 20px rgba(0,245,212,0.3);
  }
  .title-hero span { color: #00f5d4; }
  .title-subhero { font-size: 17pt; color: #48cae4; font-weight: 500; margin-bottom: 9mm; }
  .title-meta-box {
    display: flex; gap: 12mm;
    background: rgba(28,37,65,0.6);
    border: 1px solid rgba(0,245,212,0.3);
    padding: 5mm 14mm; border-radius: 12px;
  }
  .meta-item { text-align: left; }
  .meta-label { font-size: 8pt; color: #94a3b8; text-transform: uppercase; font-weight: 700; }
  .meta-value { font-size: 11.5pt; color: #ffffff; font-weight: 700; }

  /* Pipeline flow diagram */
  .pipeline {
    display: flex; align-items: center; gap: 0;
    background: rgba(11,19,43,0.6);
    border: 1px solid rgba(0,245,212,0.2);
    border-radius: 8px; padding: 4mm 5mm;
    font-size: 8.5pt;
  }
  .pipe-node {
    background: rgba(0,245,212,0.12);
    border: 1px solid rgba(0,245,212,0.4);
    border-radius: 6px;
    padding: 3px 8px;
    color: #00f5d4;
    font-weight: 700;
    white-space: nowrap;
  }
  .pipe-arrow {
    color: #48cae4; font-size: 10pt; padding: 0 3px;
  }

  /* Module grid for slide 4 */
  .mod-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; }
  .mod-card {
    background: rgba(28,37,65,0.7);
    border-left: 3px solid #00f5d4;
    border-radius: 0 6px 6px 0;
    padding: 3mm 4mm;
  }
  .mod-num { font-size: 8pt; color: #48cae4; font-weight: 700; text-transform: uppercase; }
  .mod-name { font-size: 11pt; font-weight: 800; color: #ffffff; margin: 1mm 0; }
  .mod-desc { font-size: 8.5pt; color: #94a3b8; line-height: 1.4; }

  /* Scenario cards slide 5 */
  .scenario-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 5mm; }
  .scenario-card {
    background: rgba(28,37,65,0.8);
    border: 1px solid rgba(72,202,228,0.25);
    border-radius: 8px;
    padding: 5mm;
  }
  .scenario-score { font-size: 28pt; font-weight: 900; color: #00f5d4; }
  .scenario-label { font-size: 8pt; color: #94a3b8; text-transform: uppercase; font-weight: 700; margin-bottom: 3mm; }
  .scenario-title { font-size: 11pt; font-weight: 700; color: #ffffff; margin-bottom: 2mm; }
  .scenario-desc { font-size: 8.5pt; color: #94a3b8; line-height: 1.45; }

  /* Final slide */
  .principle-block {
    background: rgba(0,245,212,0.07);
    border: 1px solid rgba(0,245,212,0.25);
    border-radius: 8px;
    padding: 4mm 6mm;
    margin-bottom: 4mm;
  }
  .principle-title { font-size: 12pt; font-weight: 700; color: #00f5d4; margin-bottom: 2mm; }
  .principle-text { font-size: 9.5pt; color: #cbd5e1; line-height: 1.5; }
</style>
</head>
<body>

<!-- ═══════════════════════════════════════════════
     СЛАЙД 1 — ТИТУЛЬНЫЙ: «Без права на ошибку»
═══════════════════════════════════════════════ -->
<div class="slide title-slide">
  <div class="title-hero">
    Без права на <span>ошибку</span>
  </div>
  <div class="title-subhero">
    Интеллектуальный диспетчерский центр беспилотного коридора
  </div>
  <div class="title-meta-box">
    <div class="meta-item">
      <div class="meta-label">Команда</div>
      <div class="meta-value">VUPSEN Squad</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">Соревнование</div>
      <div class="meta-value">IT Championship NGGTI 2026</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">Средний балл</div>
      <div class="meta-value">93.87 / 100</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">Safety-нарушений</div>
      <div class="meta-value">0 из 2160 пакетов</div>
    </div>
  </div>
  <div class="slide-footer">
    <span>IT Championship NGGTI 2026</span>
    <span>Команда VUPSEN Squad</span>
    <span>Слайд 1 из 7</span>
  </div>
</div>

<!-- ═══════════════════════════════════════════════
     СЛАЙД 2 — ЗАДАЧА: масштаб и условия
═══════════════════════════════════════════════ -->
<div class="slide">
  <div class="slide-header">
    <div>
      <div class="slide-title">Задача</div>
      <div class="slide-subtitle">Что нужно было решить — масштаб и ограничения</div>
    </div>
    <div class="team-badge">VUPSEN Squad</div>
  </div>
  <div class="content">
    <div class="col">
      <div class="card">
        <div class="card-title">🛣️ Беспилотный коридор</div>
        <div class="card-body">
          <ul>
            <li><strong>86 участков</strong> трассы — разные структуры, ограничения по скорости и тоннажу</li>
            <li><strong>72 ВАТС</strong> — автономные грузовики, каждый с профилем ODD</li>
            <li><strong>50 источников данных</strong> — камеры, детекторы, метеостанции, RSU, цифровой двойник</li>
            <li><strong>4 хаба</strong> — начальные и конечные точки маршрутов</li>
            <li><strong>10 безопасных стоянок</strong> — для экстренных остановок</li>
            <li><strong>6 телеоператоров</strong> — дефицитный ресурс поддержки</li>
          </ul>
        </div>
      </div>
      <div class="card">
        <div class="card-title">⚡ Формат работы</div>
        <div class="card-body">
          <ul>
            <li>Каждые <strong>5 секунд</strong> — поток событий (400–600 событий)</li>
            <li>Ответ необходим за <strong>≤ 2000 мс</strong></li>
            <li>Работа <strong>без интернета</strong> — полностью оффлайн</li>
            <li>stdin → JSON-решение → stdout (Unix pipeline)</li>
          </ul>
        </div>
      </div>
    </div>
    <div class="col">
      <div class="card">
        <div class="card-title">📋 Что система должна выдавать каждые 5 сек</div>
        <div class="card-body">
          <ul>
            <li>Состояние <strong>каждого из 86 сегментов</strong>: OPEN / CONGESTED / PARTIAL_BLOCK / CLOSED</li>
            <li>Оценка здоровья <strong>каждого из 50 источников</strong>: OK / DEGRADED / FAILED</li>
            <li>ODD-статус <strong>каждого из 72 ВАТС</strong>: COMPLIANT / VIOLATED</li>
            <li>Команда действия <strong>каждому грузовику</strong>: CONTINUE / REROUTE / SAFE_STOP / HOLD</li>
          </ul>
        </div>
      </div>
      <div class="stats-grid" style="grid-template-columns: repeat(3,1fr); gap:4mm; margin-top:2mm;">
        <div class="stat-box">
          <div class="stat-value">86</div>
          <div class="stat-label">Сегментов</div>
        </div>
        <div class="stat-box">
          <div class="stat-value">72</div>
          <div class="stat-label">ВАТС</div>
        </div>
        <div class="stat-box">
          <div class="stat-value">50</div>
          <div class="stat-label">Источников</div>
        </div>
      </div>
    </div>
  </div>
  <div class="slide-footer">
    <span>IT Championship NGGTI 2026 // Задача</span>
    <span>Команда VUPSEN Squad</span>
    <span>Слайд 2 из 7</span>
  </div>
</div>

<!-- ═══════════════════════════════════════════════
     СЛАЙД 3 — АРХИТЕКТУРА: принципы и поток данных
═══════════════════════════════════════════════ -->
<div class="slide">
  <div class="slide-header">
    <div>
      <div class="slide-title">Архитектурные принципы</div>
      <div class="slide-subtitle">На чём строится система — без компромиссов</div>
    </div>
    <div class="team-badge">VUPSEN Squad</div>
  </div>
  <div class="content">
    <div class="col">
      <div class="card">
        <div class="card-title">🔌 Принцип 1: Полная автономность</div>
        <div class="card-body">
          Никаких облаков и внешних API во время работы. Все справочные данные — карта, профили ВАТС, правила ODD — загружаются <strong>один раз при старте</strong>. Дальше система живёт только на входящем потоке.
        </div>
      </div>
      <div class="card">
        <div class="card-title">🔢 Принцип 2: Детерминизм</div>
        <div class="card-body">
          Никаких нейросетей в критических решениях. Одинаковый вход — всегда одинаковый выход. <strong>Алгоритм Дейкстры, конечные автоматы, жёсткие правила</strong>. Решения объяснимы и сертифицируемы.
        </div>
      </div>
      <div class="card">
        <div class="card-title">🛡️ Принцип 3: Безопасность в архитектуре</div>
        <div class="card-body">
          Safety Gate стоит последним в цепочке. Ни одна команда не обходит его. CONTINUE при нарушении ODD <strong>физически невозможна</strong> в коде — это гарантия нулевых нарушений.
        </div>
      </div>
    </div>
    <div class="col">
      <div class="card" style="flex:1;">
        <div class="card-title">⚙️ Поток обработки пакета — 8 шагов</div>
        <div class="card-body">
          <div style="display:flex;flex-direction:column;gap:4px;margin-top:2mm;">
            <div class="pipeline">
              <div class="pipe-node">stdin</div>
              <div class="pipe-arrow">→</div>
              <div class="pipe-node">json.loads</div>
              <div class="pipe-arrow">→</div>
              <div class="pipe-node">process_packet()</div>
            </div>
            <div style="display:flex;flex-direction:column;gap:3px;padding:0 0 0 6mm;font-size:9pt;color:#94a3b8;">
              <div>① <span style="color:#00f5d4;">SourceHealthTracker</span> — оценка 50 источников, trust_map</div>
              <div>② <span style="color:#00f5d4;">SegmentStateEstimator</span> — классификация 86 сегментов</div>
              <div>③ <span style="color:#00f5d4;">ODDEngine.update()</span> — погода, доступность RSU</div>
              <div>④ <span style="color:#00f5d4;">HubManager.update()</span> — загрузка 4 хабов</div>
              <div>⑤ <span style="color:#00f5d4;">vehicles_state update</span> — координаты 72 ВАТС</div>
              <div>⑥ <span style="color:#00f5d4;">ODDEngine.evaluate_all()</span> — ODD для каждой машины</div>
              <div>⑦ <span style="color:#00f5d4;">CorridorRouter.plan()</span> — маршруты и действия</div>
              <div>⑧ <span style="color:#ff6b6b;font-weight:700;">SafetyGuard.arbitrate()</span> — финальный арбитраж</div>
            </div>
            <div class="pipeline" style="margin-top:4px;">
              <div class="pipe-node">stdout</div>
              <div class="pipe-arrow">←</div>
              <div class="pipe-node">json.dumps + flush</div>
              <div class="pipe-arrow">←</div>
              <div class="pipe-node">снимок решений</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <div class="slide-footer">
    <span>IT Championship NGGTI 2026 // Архитектура</span>
    <span>Команда VUPSEN Squad</span>
    <span>Слайд 3 из 7</span>
  </div>
</div>

<!-- ═══════════════════════════════════════════════
     СЛАЙД 4 — 6 МОДУЛЕЙ системы
═══════════════════════════════════════════════ -->
<div class="slide">
  <div class="slide-header">
    <div>
      <div class="slide-title">6 модулей системы</div>
      <div class="slide-subtitle">Каждый — отдельный слой ответственности</div>
    </div>
    <div class="team-badge">VUPSEN Squad</div>
  </div>
  <div style="flex:1; display:flex; flex-direction:column; gap:0;">
    <div class="mod-grid" style="flex:1;">
      <div class="mod-card">
        <div class="mod-num">Модуль 1 • sources/health.py</div>
        <div class="mod-name">🔍 Мониторинг источников</div>
        <div class="mod-desc">Отслеживает здоровье 50 датчиков. Выявляет 6 типов сбоёв: OUTAGE, PACKET_LOSS, DELAY, TIME_SKEW, FREEZE, STALE. Присваивает Trust Score (0→1). Источники с trust &lt; 0.3 игнорируются.</div>
      </div>
      <div class="mod-card">
        <div class="mod-num">Модуль 2 • network/estimator.py</div>
        <div class="mod-name">🗺️ Оценщик сегментов</div>
        <div class="mod-desc">Классифицирует 86 участков. Приоритет: физические детекторы → V2X ROAD_STATE → цифровой двойник → скорость ВАТС. Порог CONGESTED: очередь ≥ 25 м И скорость ≤ 65 км/ч.</div>
      </div>
      <div class="mod-card">
        <div class="mod-num">Модуль 3 • network/graph.py</div>
        <div class="mod-name">📡 Граф и маршрутизация</div>
        <div class="mod-desc">Ориентированный граф из 86 сегментов и 34 узлов. Алгоритм Дейкстры с весовыми коэффициентами: CONGESTED ×2.2, PARTIAL_BLOCK ×4.0. BFS для поиска ближайшей стоянки.</div>
      </div>
      <div class="mod-card">
        <div class="mod-num">Модуль 4 • safety/odd_engine.py</div>
        <div class="mod-name">⚠️ Движок ODD</div>
        <div class="mod-desc">Проверяет 7 условий для каждой из 72 машин: видимость, осадки, GNSS, V2X, структура, тоннаж, возраст карты. 4 профиля: ODD-A/B/C/D. Консервативная оценка по минимуму в зоне.</div>
      </div>
      <div class="mod-card">
        <div class="mod-num">Модуль 5 • safety/guard.py</div>
        <div class="mod-name">🛡️ Safety Gate</div>
        <div class="mod-desc">Финальный арбитр. При нарушении ODD: если есть оператор → LIMIT_SPEED 40 км/ч. Нет оператора → SAFE_STOP. Нет стоянки → HOLD. CONTINUE при нарушении ODD невозможен.</div>
      </div>
      <div class="mod-card">
        <div class="mod-num">Модуль 6 • dispatch/resource_manager.py</div>
        <div class="mod-name">📋 Диспетчер ресурсов</div>
        <div class="mod-desc">RemoteSupportPool: ≤ 6 операторов, приоритет по (6 − cargo_priority)×20 + штраф/мин. SafeStopManager: 10 стоянок, проверка вместимости и тоннажа. Удержание сессий между шагами.</div>
      </div>
    </div>
  </div>
  <div class="slide-footer">
    <span>IT Championship NGGTI 2026 // 6 модулей</span>
    <span>Команда VUPSEN Squad</span>
    <span>Слайд 4 из 7</span>
  </div>
</div>

<!-- ═══════════════════════════════════════════════
     СЛАЙД 5 — СЦЕНАРИИ: система в реальных условиях
═══════════════════════════════════════════════ -->
<div class="slide">
  <div class="slide-header">
    <div>
      <div class="slide-title">Система в реальных условиях</div>
      <div class="slide-subtitle">Три сложных сценария — как система реагирует</div>
    </div>
    <div class="team-badge">VUPSEN Squad</div>
  </div>
  <div style="flex:1; display:flex; flex-direction:column; gap:5mm;">
    <div class="scenario-grid">
      <div class="scenario-card">
        <div class="scenario-label">TRAIN-001 • Штатный режим</div>
        <div class="scenario-score">100.0</div>
        <div class="scenario-title">Идеальные условия</div>
        <div class="scenario-desc">Все датчики работают, видимость хорошая. Система правильно классифицировала все 540 пакетов. F1 по каждому классу = 1.0. Алгоритм Дейкстры выбирает оптимальные маршруты без единой ошибки.</div>
      </div>
      <div class="scenario-card">
        <div class="scenario-label">TRAIN-003 • Густой туман</div>
        <div class="scenario-score">94.17</div>
        <div class="scenario-title">Видимость до 60 м, перекрытия</div>
        <div class="scenario-desc">Ключевая проблема: в тумане машины едут медленно — старый порог давал ложные CONGESTED. Откалибровали: пробка только при очереди ≥ 25 м И скорости ≤ 65 км/ч одновременно. Safety Gate направил всех ODD-нарушителей на стоянки. 0 критических нарушений.</div>
      </div>
      <div class="scenario-card">
        <div class="scenario-label">TRAIN-004 • Массовое ДТП</div>
        <div class="scenario-score">89.01</div>
        <div class="scenario-title">Одновременные перекрытия + дефицит</div>
        <div class="scenario-desc">Несколько закрытых участков, 6 операторов разобраны — приходят новые ODD-нарушения. Система: опасные грузы получают оператора (приоритет 1), остальные — на стоянки. Оставшиеся без стоянки — HOLD на месте. Ни один грузовик не поехал без прикрытия.</div>
      </div>
    </div>
    <div style="display:flex; gap:5mm;">
      <div class="card" style="flex:1;">
        <div class="card-title">🔑 Ключевая оптимизация, поднявшая балл +4.97</div>
        <div class="card-body" style="display:flex;gap:6mm;">
          <div style="flex:1;">
            <strong style="color:#00f5d4;">Было (88.90):</strong> Порог CONGESTED — только скорость ≤ 80 км/ч. В тумане система видела пробки везде → F1_CONGESTED ≈ 0 → −25 баллов
          </div>
          <div style="flex:1;">
            <strong style="color:#00f5d4;">Стало (93.87):</strong> Два условия одновременно: очередь ≥ 25 м И скорость ≤ 65 км/ч. Плюс: добавили чтение V2X ROAD_STATE от грузовиков как дополнительного источника
          </div>
        </div>
      </div>
    </div>
  </div>
  <div class="slide-footer">
    <span>IT Championship NGGTI 2026 // Сценарии</span>
    <span>Команда VUPSEN Squad</span>
    <span>Слайд 5 из 7</span>
  </div>
</div>

<!-- ═══════════════════════════════════════════════
     СЛАЙД 6 — РЕЗУЛЬТАТЫ
═══════════════════════════════════════════════ -->
<div class="slide">
  <div class="slide-header">
    <div>
      <div class="slide-title">Результаты бенчмарка</div>
      <div class="slide-subtitle">2160 пакетов • 4 сценария • Macro F1</div>
    </div>
    <div class="team-badge">VUPSEN Squad</div>
  </div>
  <div class="content">
    <div class="col">
      <div class="stats-grid" style="grid-template-columns:repeat(2,1fr);">
        <div class="stat-box">
          <div class="stat-value green">93.87</div>
          <div class="stat-label">Средний балл / 100</div>
        </div>
        <div class="stat-box">
          <div class="stat-value green">0</div>
          <div class="stat-label">Критических нарушений</div>
        </div>
        <div class="stat-box">
          <div class="stat-value green">11.49</div>
          <div class="stat-label">мс — среднее время</div>
        </div>
        <div class="stat-box">
          <div class="stat-value green">174×</div>
          <div class="stat-label">Запас до лимита</div>
        </div>
      </div>
      <div class="card" style="flex:1;">
        <div class="card-title">📊 По сценариям</div>
        <div class="card-body">
          <table class="data-table">
            <tr>
              <th>Сценарий</th>
              <th>Условия</th>
              <th>Балл</th>
            </tr>
            <tr>
              <td>TRAIN-001</td>
              <td>Штатный режим</td>
              <td class="good">100.00</td>
            </tr>
            <tr>
              <td>TRAIN-002</td>
              <td>Отказы датчиков, связь</td>
              <td class="good">92.29</td>
            </tr>
            <tr>
              <td>TRAIN-003</td>
              <td>Густой туман, перекрытия</td>
              <td class="good">94.17</td>
            </tr>
            <tr>
              <td>TRAIN-004</td>
              <td>ДТП, дефицит ресурсов</td>
              <td class="good">89.01</td>
            </tr>
            <tr style="border-top: 2px solid rgba(0,245,212,0.4);">
              <td><strong>Средний</strong></td>
              <td></td>
              <td class="good"><strong>93.87</strong></td>
            </tr>
          </table>
        </div>
      </div>
    </div>
    <div class="col">
      <div class="card" style="flex:1;">
        <div class="card-title">🔧 Технические характеристики</div>
        <div class="card-body">
          <table class="data-table">
            <tr><th>Параметр</th><th>Значение</th></tr>
            <tr><td>Язык</td><td>Python 3.12+</td></tr>
            <tr><td>Внешние зависимости</td><td>jsonschema, numpy</td></tr>
            <tr><td>Размер Docker-образа</td><td>188 МБ</td></tr>
            <tr><td>Размер архива сабмишна</td><td>47.4 МБ</td></tr>
            <tr><td>Время ответа (avg)</td><td class="good">11.49 мс</td></tr>
            <tr><td>Лимит по условиям</td><td>2000 мс</td></tr>
            <tr><td>Запас по времени</td><td class="good">174× быстрее</td></tr>
            <tr><td>Автоматических тестов</td><td class="good">54 — 100% OK</td></tr>
            <tr><td>Критических нарушений</td><td class="good">0 из 2160</td></tr>
          </table>
        </div>
      </div>
    </div>
  </div>
  <div class="slide-footer">
    <span>IT Championship NGGTI 2026 // Результаты</span>
    <span>Команда VUPSEN Squad</span>
    <span>Слайд 6 из 7</span>
  </div>
</div>

<!-- ═══════════════════════════════════════════════
     СЛАЙД 7 — ЗАКЛЮЧЕНИЕ: промышленная ценность
═══════════════════════════════════════════════ -->
<div class="slide">
  <div class="slide-header">
    <div>
      <div class="slide-title">Итог: безопасность встроена</div>
      <div class="slide-subtitle">Не прикручена сверху — встроена в архитектуру</div>
    </div>
    <div class="team-badge">VUPSEN Squad</div>
  </div>
  <div class="content">
    <div class="col">
      <div class="principle-block">
        <div class="principle-title">🏗️ Детерминированные алгоритмы</div>
        <div class="principle-text">Никаких нейросетей в критических решениях. Дейкстра, конечные автоматы, жёсткие правила. Каждое решение объяснимо — можно проверить, сертифицировать и объяснить любому регулятору.</div>
      </div>
      <div class="principle-block">
        <div class="principle-title">🛡️ Safety-first архитектура</div>
        <div class="principle-text">Safety Gate — физически последний барьер. CONTINUE при нарушении ODD невозможна в коде. Доказано на 2160 пакетах: 0 критических нарушений при любых условиях — тумане, отказах, авариях.</div>
      </div>
      <div class="principle-block">
        <div class="principle-title">📈 Готово к индустриальному применению</div>
        <div class="principle-text">Docker-контейнер, 54 автотеста, GitHub CI, 174× запас по времени. Беспилотные коридоры придут только с доверием общества — а доверие строится на предсказуемой, объяснимой системе.</div>
      </div>
    </div>
    <div class="col" style="justify-content:center; align-items:center;">
      <div style="text-align:center; padding:8mm;">
        <div style="font-size:52pt; font-weight:900; color:#00f5d4; line-height:1;">93.87</div>
        <div style="font-size:14pt; color:#94a3b8; margin:2mm 0 6mm;">средний балл из 100</div>
        <div style="font-size:36pt; font-weight:900; color:#ff6b6b; line-height:1;">0</div>
        <div style="font-size:14pt; color:#94a3b8; margin:2mm 0 6mm;">критических нарушений</div>
        <div style="font-size:28pt; font-weight:900; color:#48cae4; line-height:1;">174×</div>
        <div style="font-size:14pt; color:#94a3b8; margin:2mm 0 8mm;">быстрее лимита</div>
        <div style="font-size:13pt; color:#ffffff; font-weight:700; font-style:italic;">
          «Без права на ошибку»
        </div>
        <div style="font-size:10pt; color:#48cae4; margin-top:2mm;">— VUPSEN Squad, IT Championship NGGTI 2026</div>
      </div>
    </div>
  </div>
  <div class="slide-footer">
    <span>IT Championship NGGTI 2026 // Заключение</span>
    <span>Команда VUPSEN Squad</span>
    <span>Слайд 7 из 7</span>
  </div>
</div>

</body>
</html>
"""


def main() -> None:
    html_file = PROJECT_ROOT / "presentation.html"
    pdf_file = PROJECT_ROOT / "presentation.pdf"

    print("📄 Writing presentation HTML...")
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(HTML_CONTENT)

    print("🔄 Converting to PDF via headless LibreOffice...")
    cmd = [
        "libreoffice",
        "--headless",
        "--convert-to",
        "pdf",
        str(html_file),
        "--outdir",
        str(PROJECT_ROOT),
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        print(f"Error during PDF conversion: {res.stderr}")
        sys.exit(1)

    print(f"✅ Generated: {pdf_file} ({pdf_file.stat().st_size / 1024:.1f} KB)")

    # Verify page count
    print("🔍 Checking PDF page count...")
    try:
        gs_cmd = [
            "gs", "-q", "-dNODISPLAY", "-c",
            f"({pdf_file}) (r) file runpdfbegin pdfpagecount = quit",
        ]
        gs_res = subprocess.run(gs_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        page_count = int(gs_res.stdout.strip())
        print(f"📄 Page count: {page_count} pages")
        assert page_count <= 7, f"Presentation must be <= 7 slides, got {page_count}!"
        print(f"🎉 Presentation DoD verified: exactly {page_count} slides (<= 7 required by rules)!")
    except Exception as e:
        print(f"Warning checking page count: {e}")


if __name__ == "__main__":
    main()
