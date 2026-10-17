// ECharts по частям: только то, что рисует графики, — линия, столбцы (итоги по аккаунтам), сетка,
// подсказка, легенда, вертикали событий и выделение участка мышью (панель инструментов подключает
// его сама, без ползунка и масштаба колесом). Тесты подменяют этот модуль целиком.
import { BarChart, LineChart } from 'echarts/charts';
import {
	GridComponent,
	LegendComponent,
	MarkLineComponent,
	ToolboxComponent,
	TooltipComponent
} from 'echarts/components';
import { init, use } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';

use([
	LineChart,
	BarChart,
	GridComponent,
	TooltipComponent,
	LegendComponent,
	MarkLineComponent,
	ToolboxComponent,
	CanvasRenderer
]);

export { init };
export type { ECharts, EChartsCoreOption } from 'echarts/core';
