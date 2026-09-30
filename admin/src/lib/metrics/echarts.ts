// ECharts по частям: только то, что рисует графики метрик, — линия, сетка, подсказка, вертикали
// событий и выделение участка мышью (панель инструментов подключает его сама, без ползунка и
// масштаба колесом). Тесты подменяют этот модуль целиком.
import { LineChart } from 'echarts/charts';
import { GridComponent, MarkLineComponent, ToolboxComponent, TooltipComponent } from 'echarts/components';
import { init, use } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';

use([LineChart, GridComponent, TooltipComponent, MarkLineComponent, ToolboxComponent, CanvasRenderer]);

export { init };
export type { ECharts, EChartsCoreOption } from 'echarts/core';
