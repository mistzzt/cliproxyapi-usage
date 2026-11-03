// Shared Chart.js element registration; import once from any chart component.
import { Chart as ChartJS, BarElement, CategoryScale, Legend, LinearScale, Tooltip } from 'chart.js';

ChartJS.register(BarElement, CategoryScale, Legend, LinearScale, Tooltip);
