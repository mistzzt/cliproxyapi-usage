import type { ReactNode } from 'react';
import styles from './Card.module.scss';

interface CardProps {
  title?: string;
  children: ReactNode;
  action?: ReactNode;
}

export default function Card({ title, children, action }: CardProps) {
  const hasHeader = title !== undefined || action !== undefined;
  return (
    <div className={styles.card}>
      {hasHeader && (
        <div className={styles.header}>
          {title !== undefined && <span className={styles.title}>{title}</span>}
          {action !== undefined && <div className={styles.action}>{action}</div>}
        </div>
      )}
      <div className={styles.body}>{children}</div>
    </div>
  );
}
