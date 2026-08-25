import { useEffect, useState } from "react";
import { Table, Button, Alert } from "antd";
import type { ColumnsType } from "antd/es/table";
import { getHighRisk, downloadHighRiskCsv, HighRiskItem } from "../../../api/groupAnalysis";
import type { FiltersState } from "./FilterBar";

interface Props {
  filters: FiltersState;
}

export default function HighRiskTable({ filters }: Props) {
  const [data, setData] = useState<HighRiskItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sortField, setSortField] = useState<string>("red_count");

  const load = async (p: number, ps: number) => {
    setLoading(true);
    setError(null);
    try {
      const r = await getHighRisk({
        ...filters,
        page: p,
        page_size: ps,
        sort: sortField,
      });
      setData(r.items);
      setTotal(r.total);
    } catch (err: any) {
      setData([]);
      setTotal(0);
      setError(err?.message || "加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(1, 20); }, [JSON.stringify(filters), sortField]);

  const columns: ColumnsType<HighRiskItem> = [
    { title: "医院", dataIndex: "hospital_name", width: 140 },
    { title: "姓名", dataIndex: "name", width: 80 },
    { title: "性别", dataIndex: "gender", width: 60 },
    { title: "年龄", dataIndex: "age", width: 60, sorter: true },
    { title: "体检日期", dataIndex: "report_date", width: 110, sorter: true },
    { title: "整体级别", dataIndex: "overall_level", width: 80 },
    { title: "红指标数", dataIndex: "red_count", width: 80, sorter: true,
      defaultSortOrder: "descend" },
    { title: "黄指标数", dataIndex: "yellow_count", width: 80 },
    { title: "解读摘要", dataIndex: "summary_text", ellipsis: true },
  ];

  return (
    <div>
      <div style={{ marginBottom: 8, textAlign: "right" }}>
        <Button onClick={() => downloadHighRiskCsv({ ...filters, sort: sortField })}>
          导出 CSV
        </Button>
      </div>
      {error && (
        <Alert type="error" message={error} style={{ marginBottom: 8 }} showIcon />
      )}
      <Table
        rowKey={r => `${r.hospital_id}-${r.report_id}`}
        columns={columns}
        dataSource={data}
        loading={loading}
        pagination={{
          current: page, pageSize, total,
          onChange: (p, ps) => { setPage(p); setPageSize(ps); load(p, ps); },
          showSizeChanger: true,
        }}
        onChange={(_pagination, _filters, sorter) => {
          if (!Array.isArray(sorter) && sorter.field) {
            setSortField(sorter.field as string);
          }
        }}
      />
    </div>
  );
}