import 'react-day-picker/style.css';
import { DayPicker, getDefaultClassNames } from 'react-day-picker';
import type { DateRange } from 'react-day-picker';
import styles from './RangeCalendar.module.scss';

export interface RangeCalendarProps {
  value: DateRange | undefined;
  onChange: (range: DateRange | undefined) => void;
  /** Upper bound for selectable days (defaults to today). */
  today?: Date;
}

/**
 * Two-month range picker (Sunday-first) used inside the `Custom` popover.
 * Styling is applied via the v10 `classNames` element keys plus --rdp-* theme
 * overrides in the local SCSS module.
 */
export default function RangeCalendar({ value, onChange, today = new Date() }: RangeCalendarProps) {
  const defaults = getDefaultClassNames();
  return (
    <DayPicker
      mode="range"
      weekStartsOn={0}
      numberOfMonths={2}
      disabled={{ after: today }}
      selected={value}
      onSelect={onChange}
      classNames={{ root: `${defaults.root} ${styles.calendar}` }}
    />
  );
}
