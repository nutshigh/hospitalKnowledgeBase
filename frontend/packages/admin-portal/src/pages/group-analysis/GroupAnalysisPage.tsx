import { useEffect, useState } from "react";
import { Tabs, Alert, message } from "antd";
import FilterBar, { FiltersState } from "./components/FilterBar";
import OverviewCharts from "./components/OverviewCharts";
import HighRiskTable from "./components/HighRiskTable";
import { getOverview, OverviewResponse, GroupBy } from "../../api/groupAnalysis";

export default function GroupAnalysisPage() {
  const [filters, setFilters] = useState<FiltersState>({});
  const [effective, setEffective] = useState<FiltersState>({});
  const [groupBy, setGroupBy] = useState<GroupBy>("hospital");
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [activeKey, setActiveKey] = useState<"overview" | "high-risk">("overview");

  // M5: clear stale overview when groupBy changes (until user clicks 查询)
  useEffect(() => {
    setOverview(null);
    setOverviewError(null);
  }, [groupBy]);

  const submit = async () => {
    setEffective(filters);
    setLoading(true);
    setOverviewError(null);
    try {
      const r = await getOverview({ group_by: groupBy, ...filters });
      setOverview(r);
    } catch (err: any) {
      setOverview(null);
      const msg = err?.message || "加载失败";
      setOverviewError(msg);
      message.error(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: 24 }}>
      <h2>团体健康体检分析</h2>
      <FilterBar value={filters} onChange={setFilters} onSubmit={submit}
        groupBy={groupBy} onGroupByChange={setGroupBy} />
      {overviewError && (
        <Alert type="error" message={overviewError} style={{ marginBottom: 16 }}
          showIcon closable onClose={() => setOverviewError(null)} />
      )}
      {activeKey === "high-risk" ? (
        <Tabs
          activeKey={activeKey}
          onChange={k => setActiveKey(k as "overview" | "high-risk")}
          items={[
            { key: "overview", label: "概览图", children: null },
            { key: "high-risk", label: "重点人群",
              children: <HighRiskTable filters={effective} /> },
          ]}
        />
      ) : (
        <Tabs
          activeKey={activeKey}
          onChange={k => setActiveKey(k as "overview" | "high-risk")}
          items={[
            { key: "overview", label: "概览图",
              children: <OverviewCharts data={overview} loading={loading} groupBy={groupBy} /> },
            { key: "high-risk", label: "重点人群", children: null },
          ]}
        />
      )}
    </div>
  );
}