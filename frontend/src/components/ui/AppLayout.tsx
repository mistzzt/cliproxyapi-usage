import { Outlet } from 'react-router';
import AppHeader from './AppHeader';
import styles from './AppLayout.module.scss';

/** Centered, width-capped shell shared by every page; it alone owns the side gutters. */
export default function AppLayout() {
  return (
    <div className={styles.shell}>
      <AppHeader />
      <Outlet />
    </div>
  );
}
