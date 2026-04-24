/**
 * Shared Chart.js element registration.
 *
 * Import this module once from any chart component that needs these elements.
 * StatCards.tsx has its own registration (LineElement/PointElement/LinearScale/CategoryScale)
 * and is intentionally left alone — it does not import this file.
 */
import {
  Chart as ChartJS,
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale,
  Tooltip,
  Legend,
  BarElement,
} from 'chart.js';

ChartJS.register(
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale,
  Tooltip,
  Legend,
  BarElement,
);
