import styles from './Select.module.scss';

export interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps {
  value: string;
  onChange: (v: string) => void;
  options: SelectOption[];
  disabled?: boolean;
  id?: string;
  'aria-label'?: string;
}

export default function Select({ value, onChange, options, disabled = false, id, 'aria-label': ariaLabel }: SelectProps) {
  return (
    <div className={styles.wrap}>
      <select
        className={styles.select}
        value={value}
        disabled={disabled}
        id={id}
        aria-label={ariaLabel}
        onChange={(e) => onChange(e.currentTarget.value)}
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
      <span className={styles.arrow} aria-hidden="true">▾</span>
    </div>
  );
}
