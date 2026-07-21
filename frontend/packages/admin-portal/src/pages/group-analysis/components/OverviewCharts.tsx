import ReactECharts from "echarts-for-react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import { GridComponent, TooltipComponent, LegendComponent, TitleComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { Card, Spin } from "antd";
import type { OverviewResponse, GroupBy } from "../../../api/groupAnalysis";

echarts.use([BarChart, LineChart, PieChart, GridComponent, TooltipComponent,
  LegendComponent, TitleComponent, CanvasRenderer]);

interface Props {
  data: OverviewResponse | null;
  loading: boolean;
  groupBy: GroupBy;
}

export default function OverviewCharts({ data, loading, groupBy }: Props) {
  if (loading || !data) return <Spin />;
  const labels = data.rows.map(r => r.label);
  const makeBarData = (field: 'red_count' | 'yellow_count' | 'green_count') =>
    data.rows.map(r => ({
      value: r[field],
      itemStyle: r.error ? { color: "#fa8c16" } : undefined,
    }));
  const redBarData = makeBarData('red_count');
  const yellowBarData = makeBarData('yellow_count');
  const greenBarData = makeBarData('green_count');
  const rates = data.rows.map(r => Number((r.abnormal_rate * 100).toFixed(1)));

  const axisTooltip: any = {
    trigger: "axis",
    formatter: (params: any[]) => {
      if (!params?.length) return "";
      const idx = params[0].dataIndex;
      const row = data.rows[idx];
      if (row.error === "db_unavailable") {
        return `${row.label}<br/>数据库不可用`;
      }
      let result = `${row.label}<br/>`;
      for (const p of params) {
        result += `${p.marker} ${p.seriesName}: ${p.value}<br/>`;
      }
      return result;
    },
  };

  let option: any;
  if (groupBy === "hospital" || groupBy === "batch") {
    option = {
      tooltip: axisTooltip,
      legend: { data: ["红", "黄", "绿", "异常率%"] },
      xAxis: { type: "category", data: labels },
      yAxis: [
        { type: "value", name: "人数" },
        { type: "value", name: "异常率%", max: 100 },
      ],
      series: [
        { name: "红", type: "bar", stack: "t", data: redBarData, itemStyle: { color: "#ff4d4f" } },
        { name: "黄", type: "bar", stack: "t", data: yellowBarData, itemStyle: { color: "#faad14" } },
        { name: "绿", type: "bar", stack: "t", data: greenBarData, itemStyle: { color: "#52c41a" } },
        { name: "异常率%", type: "line", yAxisIndex: 1, data: rates,
          itemStyle: { color: "#1890ff" } },
      ],
    };
  } else if (groupBy === "age_group" || groupBy === "time_month") {
    option = {
      tooltip: axisTooltip,
      xAxis: { type: "category", data: labels },
      yAxis: { type: "value", name: "异常率%", max: 100 },
      series: [{ type: "line", data: rates, smooth: true,
        itemStyle: { color: "#1890ff" } }],
    };
  } else if (groupBy === "gender") {
    option = {
      tooltip: axisTooltip,
      legend: { data: ["红", "黄", "绿"] },
      xAxis: { type: "category", data: labels },
      yAxis: { type: "value" },
      series: [
        { name: "红", type: "bar", stack: "t", data: redBarData, itemStyle: { color: "#ff4d4f" } },
        { name: "黄", type: "bar", stack: "t", data: yellowBarData, itemStyle: { color: "#faad14" } },
        { name: "绿", type: "bar", stack: "t", data: greenBarData, itemStyle: { color: "#52c41a" } },
      ],
    };
  } else {
    option = {};
  }

  const topItems = groupBy === "hospital" || groupBy === "batch"
    ? data.rows.flatMap(r => r.top_abnormal_items || []).slice(0, 10)
    : [];

  return (
    <div>
      <Card title="团体健康体检分析">
        <ReactECharts echarts={echarts} option={option} style={{ height: 320 }} />
      </Card>
      {topItems.length > 0 && (
        <Card title="Top 异常指标" style={{ marginTop: 16 }}>
          <ReactECharts echarts={echarts} option={{
            tooltip: { trigger: "axis" },
            xAxis: { type: "value" },
            yAxis: { type: "category", data: topItems.map(t => t.item) },
            series: [{ type: "bar", data: topItems.map(t => t.red_count),
              itemStyle: { color: "#ff4d4f" } }],
          }} style={{ height: 240 }} />
        </Card>
      )}
    </div>
  );
}
