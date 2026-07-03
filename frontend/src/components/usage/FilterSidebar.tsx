import { useMemo, useState, useEffect, useRef, useCallback } from 'react';
import type { ReactNode } from 'react';
import { parseISO, format } from 'date-fns';
import * as Popover from '@radix-ui/react-popover';
import type { DateRange } from 'react-day-picker';
import { CHART_MAX_LINES } from '@/pages/usage-constants';
import type { RangeSpec } from '@/types/api';
import {
  stepAnchor,
  isCurrentPeriod,
  anchorFor,
  formatCalendarLabel,
} from '@/utils/rangeResolver';
import Button from '@/components/ui/Button';
import RangeCalendar from '@/components/usage/RangeCalendar';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import styles from './FilterSidebar.module.scss';

export interface FilterSidebarProps {
  range: RangeSpec;
  onRangeChange: (r: RangeSpec) => void;
  models: string[];
  selectedModels: string[];
  onModelsChange: (next: string[]) => void;
  apiKeys: string[];
  selectedApiKeys: string[];
  onApiKeysChange: (next: string[]) => void;
  onRefresh: () => void;
  collapsed: boolean;
  onCollapsedChange: (next: boolean) => void;
  mobileOpen?: boolean;
  onMobileClose?: () => void;
}

interface SidebarBodyProps {
  range: RangeSpec;
  onRangeChange: (r: RangeSpec) => void;
  models: string[];
  selectedModels: string[];
  onModelsChange: (next: string[]) => void;
  apiKeys: string[];
  selectedApiKeys: string[];
  onApiKeysChange: (next: string[]) => void;
  onRefresh: () => void;
}

interface MultiSelectProps {
  id: string;
  label: string;
  searchPlaceholder: string;
  allLabel: string;
  items: string[];
  selected: string[];
  onChange: (next: string[]) => void;
  /** Optional cap: when reached, further boxes are disabled. */
  maxSelection?: number;
}

function FilterIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 18 18"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M2 4h14M5 9h8M8 14h2"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

type CalendarUnit = 'day' | 'week' | 'month';

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={`${styles.chip} ${active ? styles.chipActive : ''}`}
      aria-pressed={active}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function RangeControl({
  range,
  onRangeChange,
}: {
  range: RangeSpec;
  onRangeChange: (r: RangeSpec) => void;
}) {
  const now = new Date();
  const [customOpen, setCustomOpen] = useState(false);
  const [draft, setDraft] = useState<DateRange | undefined>(undefined);

  const isRolling = (preset: '7h' | '24h') =>
    range.kind === 'rolling' && range.preset === preset;
  const isCalendarLive = (unit: CalendarUnit) =>
    range.kind === 'calendar' && range.unit === unit && isCurrentPeriod(unit, range.anchor, now);

  const selectCalendar = (unit: CalendarUnit) =>
    onRangeChange({ kind: 'calendar', unit, anchor: anchorFor(now) });

  const handleCustomOpenChange = (open: boolean) => {
    if (open) {
      setDraft(
        range.kind === 'custom'
          ? { from: parseISO(range.startDate), to: parseISO(range.endDate) }
          : undefined,
      );
    }
    setCustomOpen(open);
  };

  const applyCustom = () => {
    if (!draft?.from) return;
    const to = draft.to ?? draft.from;
    onRangeChange({
      kind: 'custom',
      startDate: format(draft.from, 'yyyy-MM-dd'),
      endDate: format(to, 'yyyy-MM-dd'),
    });
    setCustomOpen(false);
  };

  return (
    <div className={styles.section}>
      <span className={styles.sectionLabel}>Range</span>
      <div className={styles.chipRow} role="group" aria-label="Time range">
        <Chip active={isRolling('7h')} onClick={() => onRangeChange({ kind: 'rolling', preset: '7h' })}>
          7h
        </Chip>
        <Chip
          active={isRolling('24h')}
          onClick={() => onRangeChange({ kind: 'rolling', preset: '24h' })}
        >
          24h
        </Chip>
        <Chip active={isCalendarLive('day')} onClick={() => selectCalendar('day')}>
          Today
        </Chip>
        <Chip active={isCalendarLive('week')} onClick={() => selectCalendar('week')}>
          This week
        </Chip>
        <Chip active={isCalendarLive('month')} onClick={() => selectCalendar('month')}>
          This month
        </Chip>
        <Chip active={range.kind === 'all'} onClick={() => onRangeChange({ kind: 'all' })}>
          All
        </Chip>

        <Popover.Root open={customOpen} onOpenChange={handleCustomOpenChange}>
          <Popover.Trigger asChild>
            <button
              type="button"
              className={`${styles.chip} ${range.kind === 'custom' ? styles.chipActive : ''}`}
              aria-pressed={range.kind === 'custom'}
            >
              Custom
            </button>
          </Popover.Trigger>
          <Popover.Portal>
            <Popover.Content
              className={styles.popover}
              sideOffset={8}
              align="start"
              collisionPadding={12}
            >
              <RangeCalendar value={draft} onChange={setDraft} today={now} />
              <div className={styles.popoverActions}>
                <Button variant="secondary" onClick={() => setCustomOpen(false)}>
                  Cancel
                </Button>
                <Button onClick={applyCustom} disabled={!draft?.from}>
                  Apply
                </Button>
              </div>
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>
      </div>

      {range.kind === 'calendar' && (
        <div className={styles.stepper}>
          <button
            type="button"
            className={styles.stepButton}
            aria-label="Previous period"
            onClick={() =>
              onRangeChange({ ...range, anchor: stepAnchor(range.unit, range.anchor, -1) })
            }
          >
            ‹
          </button>
          <span className={styles.stepLabel}>
            {formatCalendarLabel(range.unit, range.anchor)}
          </span>
          <button
            type="button"
            className={styles.stepButton}
            aria-label="Next period"
            disabled={isCurrentPeriod(range.unit, range.anchor, now)}
            onClick={() =>
              onRangeChange({ ...range, anchor: stepAnchor(range.unit, range.anchor, 1) })
            }
          >
            ›
          </button>
        </div>
      )}
    </div>
  );
}

function MultiSelectSection({
  id,
  label,
  searchPlaceholder,
  allLabel,
  items,
  selected,
  onChange,
  maxSelection,
}: MultiSelectProps) {
  const [search, setSearch] = useState('');
  const allChecked = selected.length === 1 && selected[0] === 'all';

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return items;
    return items.filter((m) => m.toLowerCase().includes(query));
  }, [items, search]);

  const handleAllChange = (checked: boolean) => {
    onChange(checked ? ['all'] : []);
  };

  const handleItemChange = (item: string, checked: boolean) => {
    const base = selected.filter((s) => s !== 'all');
    if (checked) {
      if (maxSelection !== undefined && base.length >= maxSelection) return;
      onChange([...base, item]);
    } else {
      onChange(base.filter((s) => s !== item));
    }
  };

  const selectionCount = selected.filter((s) => s !== 'all').length;

  return (
    <div className={styles.section}>
      <label className={styles.sectionLabel} htmlFor={`${id}-search`}>
        {label}
      </label>
      <input
        id={`${id}-search`}
        type="search"
        className={styles.modelSearch}
        value={search}
        onChange={(e) => setSearch(e.currentTarget.value)}
        placeholder={searchPlaceholder}
      />
      <div className={styles.modelList} role="group" aria-label={label}>
        <label className={styles.modelItem}>
          <input
            type="checkbox"
            className={styles.modelCheckbox}
            checked={allChecked}
            onChange={(e) => handleAllChange(e.currentTarget.checked)}
            aria-label={allLabel}
          />
          <span className={styles.modelName}>{allLabel}</span>
        </label>

        <div className={styles.modelDivider} role="separator" />

        {filtered.map((item) => {
          const isChecked = !allChecked && selected.includes(item);
          const wouldExceedCap =
            maxSelection !== undefined &&
            !isChecked &&
            !allChecked &&
            selectionCount >= maxSelection;
          return (
            <label
              key={item}
              className={`${styles.modelItem} ${
                allChecked || wouldExceedCap ? styles.modelItemDisabled : ''
              }`}
              title={wouldExceedCap ? `Maximum ${maxSelection} selected` : undefined}
            >
              <input
                type="checkbox"
                className={styles.modelCheckbox}
                checked={isChecked}
                disabled={allChecked || wouldExceedCap}
                onChange={(e) => handleItemChange(item, e.currentTarget.checked)}
                aria-label={item}
              />
              <span className={styles.modelName}>{item}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}

function SidebarBody({
  range,
  onRangeChange,
  models,
  selectedModels,
  onModelsChange,
  apiKeys,
  selectedApiKeys,
  onApiKeysChange,
  onRefresh,
}: SidebarBodyProps) {
  return (
    <div id="filter-sidebar-body">
      <RangeControl range={range} onRangeChange={onRangeChange} />

      <MultiSelectSection
        id="filter-sidebar-models"
        label="Models"
        searchPlaceholder="Search models"
        allLabel="All"
        items={models}
        selected={selectedModels}
        onChange={onModelsChange}
        maxSelection={CHART_MAX_LINES}
      />

      <MultiSelectSection
        id="filter-sidebar-api-keys"
        label="API keys"
        searchPlaceholder="Search API keys"
        allLabel="All"
        items={apiKeys}
        selected={selectedApiKeys}
        onChange={onApiKeysChange}
      />

      <div className={styles.refreshSection}>
        <Button onClick={onRefresh}>Refresh</Button>
      </div>
    </div>
  );
}

function getFocusables(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((el) => !el.closest('[hidden]') && el.offsetParent !== null);
}

export default function FilterSidebar(props: FilterSidebarProps) {
  const isMobile = useMediaQuery('(max-width: 768px)');

  const drawerRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const previousActiveRef = useRef<HTMLElement | null>(null);

  const { selectedModels, selectedApiKeys } = props;
  const modelsAll = selectedModels.length === 1 && selectedModels[0] === 'all';
  const apiKeysAll = selectedApiKeys.length === 1 && selectedApiKeys[0] === 'all';
  const selectionCount =
    selectedModels.filter((s) => s !== 'all').length +
    selectedApiKeys.filter((s) => s !== 'all').length;
  const showBadge = !(modelsAll && apiKeysAll) && selectionCount > 0;

  const isDrawerOpen = isMobile && props.mobileOpen === true;

  useEffect(() => {
    if (!isDrawerOpen) return;
    previousActiveRef.current = document.activeElement as HTMLElement | null;
    const id = requestAnimationFrame(() => {
      closeButtonRef.current?.focus();
    });
    return () => {
      cancelAnimationFrame(id);
      previousActiveRef.current?.focus();
      previousActiveRef.current = null;
    };
  }, [isDrawerOpen]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (!isDrawerOpen) return;

      if (e.key === 'Escape') {
        e.preventDefault();
        props.onMobileClose?.();
        return;
      }

      if (e.key === 'Tab' && drawerRef.current) {
        const focusables = getFocusables(drawerRef.current);
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey) {
          if (document.activeElement === first) {
            e.preventDefault();
            last?.focus();
          }
        } else {
          if (document.activeElement === last) {
            e.preventDefault();
            first?.focus();
          }
        }
      }
    },
    [isDrawerOpen, props],
  );

  useEffect(() => {
    if (!isDrawerOpen) return;
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isDrawerOpen, handleKeyDown]);

  const bodyProps: SidebarBodyProps = {
    range: props.range,
    onRangeChange: props.onRangeChange,
    models: props.models,
    selectedModels: props.selectedModels,
    onModelsChange: props.onModelsChange,
    apiKeys: props.apiKeys,
    selectedApiKeys: props.selectedApiKeys,
    onApiKeysChange: props.onApiKeysChange,
    onRefresh: props.onRefresh,
  };

  if (props.collapsed) {
    return (
      <>
        <aside
          className={`${styles.sidebar} ${styles.sidebarCollapsed}`}
          aria-label="Filters"
        >
          <button
            className={styles.toggleButton}
            aria-expanded={!props.collapsed}
            aria-controls="filter-sidebar-body"
            aria-label="Expand filters"
            onClick={() => props.onCollapsedChange(false)}
          >
            ≡
          </button>
          <div className={styles.railIcon}>
            <FilterIcon />
            {showBadge && <span className={styles.badge}>{selectionCount}</span>}
          </div>
        </aside>

        {isDrawerOpen && (
          <>
            <div
              className={styles.backdrop}
              role="presentation"
              onClick={props.onMobileClose}
            />
            <div
              ref={drawerRef}
              className={styles.drawer}
              role="dialog"
              aria-modal="true"
              aria-label="Filters"
            >
              <div className={styles.drawerHeader}>
                <h2 className={styles.heading}>Filters</h2>
                <button
                  ref={closeButtonRef}
                  className={styles.closeButton}
                  aria-label="Close filters"
                  onClick={props.onMobileClose}
                >
                  ✕
                </button>
              </div>
              <SidebarBody {...bodyProps} />
            </div>
          </>
        )}
      </>
    );
  }

  return (
    <>
      <aside className={styles.sidebar} aria-label="Filters">
        <div className={styles.headingRow}>
          <h2 className={styles.heading}>Filters</h2>
          <button
            className={styles.toggleButton}
            aria-expanded={!props.collapsed}
            aria-controls="filter-sidebar-body"
            aria-label="Collapse filters"
            onClick={() => props.onCollapsedChange(true)}
          >
            ≡
          </button>
        </div>

        {!isMobile && <SidebarBody {...bodyProps} />}
      </aside>

      {isDrawerOpen && (
        <>
          <div
            className={styles.backdrop}
            role="presentation"
            onClick={props.onMobileClose}
          />
          <div
            ref={drawerRef}
            className={styles.drawer}
            role="dialog"
            aria-modal="true"
            aria-label="Filters"
          >
            <div className={styles.drawerHeader}>
              <h2 className={styles.heading}>Filters</h2>
              <button
                ref={closeButtonRef}
                className={styles.closeButton}
                aria-label="Close filters"
                onClick={props.onMobileClose}
              >
                ✕
              </button>
            </div>
            <SidebarBody {...bodyProps} />
          </div>
        </>
      )}
    </>
  );
}
