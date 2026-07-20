"""一次性脚本：将 CM3KG 数据导入 Neo4j。

用法:
    cd backend
    .venv/bin/python scripts/import_cm3kg.py --data /data/data/medical.csv
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.rag.kg_client import kg_client


def main():
    parser = argparse.ArgumentParser(description="Import CM3KG CSV into Neo4j")
    parser.add_argument("--data", default="/data/data/medical.csv",
                        help="Path to medical.csv")
    args = parser.parse_args()

    if not os.path.exists(args.data):
        print(f"Error: {args.data} not found")
        sys.exit(1)

    print(f"Importing from {args.data} ...")
    stats = kg_client.import_cm3kg(args.data)
    print("Import complete:")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
