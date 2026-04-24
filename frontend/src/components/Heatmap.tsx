import { useRef } from 'react';
import type { HeatmapDay } from '../lib/types';

interface Props {
  days: HeatmapDay[];
}

function cellColor(count: number, max: number): string {
  if (count === 0) return '#1f2937';           // gray-800 empty
  const t = count / Math.max(max, 1);
  if (t < 0.25) return '#312e81';             // indigo-900
  if (t < 0.5)  return '#4338ca';             // indigo-700
  if (t < 0.75) return '#6366f1';             // indigo-500
  return '#a5b4fc';                            // indigo-300 hottest
}

export function Heatmap({ days }: Props) {
  const tooltipRef = useRef<HTMLDivElement>(null);
  const max = Math.max(1, ...days.map(d => d.count));

  // Group into columns of 7
  const COLS = Math.ceil(days.length / 7);
  const columns: HeatmapDay[][] = [];
  for (let c = 0; c < COLS; c++) {
    columns.push(days.slice(c * 7, c * 7 + 7));
  }

  const startLabel = days[0]?.date.slice(0, 7) ?? '';
  const endLabel   = days[days.length - 1]?.date.slice(0, 7) ?? '';

  function showTooltip(e: React.MouseEvent, day: HeatmapDay) {
    const el = tooltipRef.current;
    if (!el) return;
    el.textContent = `${day.date}: ${day.count} event${day.count !== 1 ? 's' : ''}`;
    el.style.left = `${e.clientX + 12}px`;
    el.style.top  = `${e.clientY - 32}px`;
    el.classList.remove('hidden');
  }

  function hideTooltip() {
    tooltipRef.current?.classList.add('hidden');
  }

  function moveTooltip(e: React.MouseEvent) {
    const el = tooltipRef.current;
    if (!el || el.classList.contains('hidden')) return;
    el.style.left = `${e.clientX + 12}px`;
    el.style.top  = `${e.clientY - 32}px`;
  }

  return (
    <div>
      <div className="flex gap-0.5 overflow-x-auto pb-1">
        {columns.map((col, ci) => (
          <div key={ci} className="flex flex-col gap-0.5">
            {col.map((day) => (
              <div
                key={day.date}
                style={{ width: 10, height: 10, borderRadius: 2, backgroundColor: cellColor(day.count, max) }}
                onMouseEnter={e => showTooltip(e, day)}
                onMouseMove={moveTooltip}
                onMouseLeave={hideTooltip}
                className="cursor-default flex-shrink-0"
              />
            ))}
          </div>
        ))}
      </div>
      <div className="flex justify-between text-xs text-gray-600 mt-1 select-none">
        <span>{startLabel}</span>
        <span>{endLabel}</span>
      </div>
      {/* Global tooltip — positioned fixed so it escapes any overflow:hidden parent */}
      <div
        ref={tooltipRef}
        className="hidden fixed z-50 px-2 py-1 text-xs bg-gray-900 border border-gray-700
                   rounded shadow-lg pointer-events-none text-gray-200 whitespace-nowrap"
      />
    </div>
  );
}
