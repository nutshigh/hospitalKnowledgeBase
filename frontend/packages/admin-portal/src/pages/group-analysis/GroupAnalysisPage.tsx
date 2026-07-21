import { useState } from "react";
import { Tabs } from "antd";
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

  const submit = async () => {
    setEffective(filters);
    setLoading(true);
    try {
      const r = await getOverview({ group_by: groupBy, ...filters });
      setOverview(r);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: 24 }}>
      <h2>团体健康体检分析</h2>
      <FilterBar value={filters} onChange={setFilters} onSubmit={submit}
        groupBy={groupBy} onGroupByChange={setGroupBy} />
      <Tabs items={[
        {
          key: "overview",
          label: "概览图",
          children: <OverviewCharts data={overview} loading={loading} groupBy={groupBy} />,
        },
        {
          key: "high-risk",
          label: "重点人群",
          children: <HighRiskTable filters={effective} />,
        },
      ]} />
    </div>
  );
}
