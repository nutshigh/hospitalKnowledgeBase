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
  const reds = data.rows.map(r => r.red_count);
  const yellows = data.rows.map(r => r.yellow_count);
  const greens = data.rows.map(r => r.green_count);
  const rates = data.rows.map(r => Number((r.abnormal_rate * 100).toFixed(1)));

  let option: any;
  if (groupBy === "hospital" || groupBy === "batch") {
    option = {
      tooltip: { trigger: "axis" },
      legend: { data: ["红", "黄", "绿", "异常率%"] },
      xAxis: { type: "category", data: labels },
      yAxis: [
        { type: "value", name: "人数" },
        { type: "value", name: "异常率%", max: 100 },
      ],
      series: [
        { name: "红", type: "bar", stack: "t", data: reds, itemStyle: { color: "#ff4d4f" } },
        { name: "黄", type: "bar", stack: "t", data: yellows, itemStyle: { color: "#faad14" } },
        { name: "绿", type: "bar", stack: "t", data: greens, itemStyle: { color: "#52c41a" } },
        { name: "异常率%", type: "line", yAxisIndex: 1, data: rates,
          itemStyle: { color: "#1890ff" } },
      ],
    };
  } else if (groupBy === "age_group" || groupBy === "time_month") {
    option = {
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: labels },
      yAxis: { type: "value", name: "异常率%", max: 100 },
      series: [{ type: "line", data: rates, smooth: true,
        itemStyle: { color: "#1890ff" } }],
    };
  } else if (groupBy === "gender") {
    option = {
      tooltip: { trigger: "axis" },
      legend: { data: ["红", "黄", "绿"] },
      xAxis: { type: "category", data: labels },
      yAxis: { type: "value" },
      series: [
        { name: "红", type: "bar", stack: "t", data: reds, itemStyle: { color: "#ff4d4f" } },
        { name: "黄", type: "bar", stack: "t", data: yellows, itemStyle: { color: "#faad14" } },
        { name: "绿", type: "bar", stack: "t", data: greens, itemStyle: { color: "#52c41a" } },
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
