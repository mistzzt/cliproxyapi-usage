import { NavLink } from 'react-router';
import ThemeToggle from './ThemeToggle';
import styles from './AppHeader.module.scss';

export default function AppHeader() {
  return (
    <nav className={styles.nav}>
      <div className={styles.links}>
        <NavLink
          to="/"
          end
          className={({ isActive }) => `${styles.link}${isActive ? ` ${styles.active}` : ''}`}
        >
          Usage
        </NavLink>
        <NavLink
          to="/quota"
          className={({ isActive }) => `${styles.link}${isActive ? ` ${styles.active}` : ''}`}
        >
          Quota
        </NavLink>
      </div>
      <ThemeToggle />
    </nav>
  );
}
